import argparse
from genericpath import isfile
import os
import pathlib
from tkinter import E
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from astropy.coordinates import SkyCoord
from astropy.time import Time
import cv2
import numpy as np
import subprocess
import warnings
# UTC to BJD converter import
from barycorrpy import utc_tdb
from pandas import isna

warnings.simplefilter('ignore', category=FITSFixedWarning)

def runsolving(ra, dec, infile, outfile):
    try:
        rslt = subprocess.run(["solve-field", infile,
            "--no-plots", "--overwrite",
            "--ra", str(ra),
            "--dec", str(dec),
            "--radius", "5",
            "--fits-image", "--guess-scale",
            "--new-fits", outfile ], 
            timeout=30, capture_output=True)
        if rslt.returncode != 0:
            print("Error solving %s - skipping" % f)
            return False
        return True
    except subprocess.TimeoutExpired:
        print("Timeout solving %s - skipping" % f)
        return False

# Initialize parser
parser = argparse.ArgumentParser()
# Add input argument
parser.add_argument("-d", "--darks", help = "Dark files source directory");
parser.add_argument("-s", "--science", help = "Science files source directory");
# Adding output argument
parser.add_argument("-o", "--output", help = "Output directory") 
# Add flags (default is grey - others for monochrome)
parser.add_argument('-r', "--red", action='store_true')
parser.add_argument('-g', "--green", action='store_true')
parser.add_argument('-b', "--blue", action='store_true')
parser.add_argument("-G", "--gray", action='store_true')
parser.add_argument("-B", "--blueblock", action='store_true')

# Read arguments from command line
try:
    args = parser.parse_args()
except argparse.ArgumentError:
    os.exit(1)
outputdir='output'
if args.output: 
    outputdir = args.output
darksrcdir='darks'
if args.darks:
    darksrcdir = args.darks 
sciencesrcdir='science'
if args.science:
    sciencesrcdir = args.science
# Make output directory, if needed
pathlib.Path(outputdir).mkdir(parents=True, exist_ok=True)
darkpath = os.path.join(outputdir, "darks")
pathlib.Path(darkpath).mkdir(parents=True, exist_ok=True)
sciencepath = os.path.join(outputdir, "science")
pathlib.Path(sciencepath).mkdir(parents=True, exist_ok=True)
badsciencepath = os.path.join(outputdir, "science-rej")
pathlib.Path(badsciencepath).mkdir(parents=True, exist_ok=True)
tmppath = os.path.join(outputdir, "tmp")
pathlib.Path(tmppath).mkdir(parents=True, exist_ok=True)

togray = False
blueblock = False
tobayer = False
coloridx = 0
fltname='bayer'
if args.red:
    coloridx = 2   # Red
    print("Produce red channel FITS files")
    fltname='TR'
elif args.green:
    coloridx = 1   # Green
    print("Produce green channel FITS files")
    fltname='TG'
elif args.blue:
    coloridx = 0   # Blue
    print("Produce blue channel FITS files")
    fltname='TB'
elif args.blueblock:
    blueblock = True
    print("Produce blue-blocked grayscale FITS files")
    fltname='CBB'
elif args.gray:
    togray = True
    print("Produce grayscale FITS files")
    fltname='CV'
else:
    tobayer = True
    print("Produce Bayer FITS files")
    fltname='BAYER'
darkfiles = []
# Go through the darks
for path in os.listdir(darksrcdir):
    dfile = os.path.join(darksrcdir, path)
    if (path.startswith('.')): continue
    # check if current path is a file
    if os.path.isfile(dfile):
        darkfiles.append(path)
darkfiles.sort()
# Go through the lights
lightfiles = []
for path in os.listdir(sciencesrcdir):
    if (path.startswith('.')): continue
    dfile = os.path.join(sciencesrcdir, path)
    # check if current path is a file
    if os.path.isfile(dfile):
        lightfiles.append(path)
lightfiles.sort()
dark = fits.HDUList()

# Build dark frame, if we have any to work with
if len(darkfiles) > 0:
    for f in darkfiles:
        try:
            dfile = os.path.join(darksrcdir, f)
            # Load file into list of HDU list 
            with fits.open(dfile) as hduList:
                # Use first one as base
                if len(dark) == 0:
                    darkaccum = np.zeros((0,) + hduList[0].data.shape)
                    dark.append(hduList[0].copy())
                darkaccum = np.append(darkaccum, [ hduList[0].data ], axis=0)
                hduList.writeto(os.path.join(darkpath, f), overwrite=True)
        except OSError:
            print("Error: file %s" % f)        
    # Now compute median for each pixel
    darkaccum = np.median(darkaccum, axis=0)
    dark[0].data = darkaccum.astype(np.uint16)
    # And write output dark
    dark.writeto(os.path.join(darkpath, "master-dark.fits"), overwrite=True)

cnt = 0
solvedcnt = 0
timeaccumlist = []
timeaccumstart = 0
timeaccumra = 0
timeaccumdec = 0
mjdobs = 0
mjdend = 0

for f in lightfiles:
    try:
        lfile = os.path.join(sciencesrcdir, f)
        # Load file into list of HDU list 
        with fits.open(lfile) as hduList:
            # First science? get center as target
            if (cnt == 0):
                fovRA = hduList[0].header['FOVRA']
                fovDec = hduList[0].header['FOVDEC']
                fov = SkyCoord(fovRA, fovDec, frame='icrs', unit='deg')
                print("center of FOV for first science: RA={0}, DEC={1}".format(fovRA, fovDec))
                obsLongitude = hduList[0].header['LONGITUD']
                obsLatitude = hduList[0].header['LATITUDE']
                obsAltitude = hduList[0].header['ALTITUDE']
                print("Observatory: Lat={0} deg, Lon={1} deg, Alt={2} meters".format(obsLatitude, obsLongitude, obsAltitude))
            # First, calibrate image
            if len(dark) > 0:
                # Clamp the data with the dark from below, so we can subtract without rollover
                np.maximum(hduList[0].data, dark[0].data, out=hduList[0].data)
                # And subtract the dark
                np.subtract(hduList[0].data, dark[0].data, out=hduList[0].data)
            # Now debayer into grayscale                
            if togray:
                dst = cv2.cvtColor(hduList[0].data, cv2.COLOR_BayerRG2GRAY)
                for idx, val in enumerate(dst):
                    hduList[0].data[idx] = val
            elif blueblock:
                # Demosaic the image
                dst = cv2.cvtColor(hduList[0].data, cv2.COLOR_BayerRG2BGR)
                for idx, val in enumerate(dst):
                    val[:,0] = 0    # Zero out blue
                dst = cv2.cvtColor(dst, cv2.COLOR_BGR2GRAY)
                for idx, val in enumerate(dst):
                    hduList[0].data[idx] = val
            else:
                # Demosaic the image
                dst = cv2.cvtColor(hduList[0].data, cv2.COLOR_BayerRG2BGR)
                for idx, val in enumerate(dst):
                    hduList[0].data[idx] = val[:,coloridx]
            # Compute BJD times
            mjdtimes = np.array([hduList[0].header['MJD-MID']])
            bjdtimes = utc_tdb.JDUTC_to_BJDTDB(mjdtimes + 2400000.5, ra=fovRA, dec=fovDec,
                        lat=obsLatitude, longi=obsLongitude, alt=obsAltitude)[0]
            hduList[0].header.set('BJD_TDB', bjdtimes[0], "barycentric Julian date of the mid obs")
            # Add bayer header if leaving as bayer file
            if tobayer:
                hduList[0].header.set('BAYERPAT', 'RGGB')
                hduList[0].header.set('XBAYROFF', 0)
                hduList[0].header.set('YBAYROFF', 0)
            rslt = True
            newfname = "science-{1}-{0:05d}.fits".format(cnt, fltname)
            newfits = os.path.join(sciencepath, newfname)
            # Write to temporary file so that we can run solve-field to
            # set WCS data
            hduList.writeto(os.path.join(tmppath, "tmp.fits"), overwrite=True)
            # Now run solve-field to generate final file
            rslt = runsolving(hduList[0].header['FOVRA'], hduList[0].header['FOVDEC'],
                os.path.join(tmppath, "tmp.fits"), newfits )
            if rslt == True:
                # Read new file - see if we are still in frame
                with fits.open(newfits) as hduListNew:
                    w = WCS(hduListNew[0].header)
                    shape = hduList[0].data.shape
                    x, y = w.world_to_pixel(fov)
                    print("Solved %s:  target at %f, %f" % (newfits, x, y))
                    # If out of range, drop the frame
                    if (x < 0) or (x >= shape[0]) or (y < 0) or (y >= shape[1]):
                        rslt = False;
                        print("Rejecting - target out of frame" % f)  
            if rslt == False:
                print("Error solving %s - skipping" % f)
                hduList.writeto(os.path.join(badsciencepath, newfname), overwrite=True)
            else:
                tobs = hduList[0].header['MJD-OBS'] * 24 * 60 # MJD in minutes
                solvedcnt = solvedcnt + 1
            cnt = cnt + 1
    except OSError as e:
        print("Error: file %s - %s (%s)" % (f, e.__class__, e))     

print("Processed %d out of %d files into destination '%s'" % (solvedcnt, cnt, outputdir))

