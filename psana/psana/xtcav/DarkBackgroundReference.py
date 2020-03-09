
import logging
logger = logging.getLogger(__name__)

import copy
import os
import time
#import psana
from psana import DataSource
import numpy as np
import glob
import sys
import getopt
import warnings
import psana.xtcav.UtilsPsana as xtup
from psana.xtcav.FileInterface import Load as constLoad
from psana.xtcav.FileInterface import Save as constSave
from psana.xtcav.CalibrationPaths import *
import psana.xtcav.Constants as cons
from psana.xtcav.Utils import namedtuple, ROIMetrics  
from psana.pyalgos.generic.NDArrUtils import info_ndarr
"""
    Class that generates a dark background image for XTCAV reconstruction purposes. Essentially takes valid
    dark reference images and averages them to find the "average" camera background. It is recommended to use a 
    large number of shots so that spikes in energy levels can get rounded out. 
    Arguments:
        experiment (str): String with the experiment reference to use. E.g. 'amoc8114'
        run (str): String with a run number. E.g. '123' 
        max_shots (int): Maximum number of images to use for the reference.
        calibration_path (str): Custom calibration directory in case the default is not intended to be used.
        validity_range (tuple): If not set, the validity range for the reference will go from the 
        first run number used to generate the reference and the last run.
"""

class DarkBackgroundReference():
    def __init__(self, 
        fname='/reg/g/psdm/detector/data2_test/xtc/data-amox23616-r0104-e000400-xtcav.xtc2',
        experiment='amox23616', 
        run_number='104', 
        max_shots=400, 
        start_image=0,
        validity_range=None, 
        calibration_path='',
        save_to_file=True):

        #fmt='%(asctime)s %(name)s %(lineno)d %(levelname)s: %(message)s' # '%(message)s'
        fmt='[%(levelname).1s] L%(lineno)04d : %(message)s'
        logging.basicConfig(format=fmt, datefmt='%Y-%m-%dT%H:%M:%S', level=logging.DEBUG)


        self.image=None
        self.ROI=None
        self.n=0

        self.parameters = DarkBackgroundParameters(
            experiment = experiment, max_shots = max_shots, run_number = run_number, 
            validity_range = validity_range, calibration_path = calibration_path)

        warnings.filterwarnings('always',module='Utils',category=UserWarning)
        warnings.filterwarnings('ignore',module='Utils',category=RuntimeWarning, message="invalid value encountered in divide")
        
        """
        After setting all the parameters, this method has to be called to generate the dark reference and 
        save it in the proper location. 
        """
        logger.info('dark background reference')
        logger.info('\t Data file: %s' % fname)
        logger.info('\t Experiment: %s' % self.parameters.experiment)
        logger.info('\t Run: %s' % self.parameters.run_number)
        logger.info('\t Valid shots to process: %d' % self.parameters.max_shots)
        logger.info('\t Detector name: %s' % cons.SRC)
        
        #Loading the dataset from the "dark" run, this way of working should be compatible with both xtc and hdf5 files
        ds=DataSource(files=fname)
        
        run = next(ds.runs())
        #Camera and type for the xtcav images
        xtcav_camera = run.Detector(cons.SRC)
        logger.info('\t RunInfo expt: %s runnum: %d\n' % (run.expt, run.runnum))

        #Stores for environment variables    
        #configStore=dataSource.env().configStore()
        #epicsStore=dataSource.env().epicsStore()

        roi_xtcav, first_image = self._getCalibrationValues(run, xtcav_camera, start_image)
        logger.info('\t roi_xtcav: '+str(roi_xtcav))
        accumulator_xtcav = np.zeros((roi_xtcav.yN, roi_xtcav.xN), dtype=np.float64)

        n=0 #Counter for the total number of xtcav images processed 
        for nev,evt in enumerate(run.events()):

            #print('Event %03d'%nev, end='')

            img = xtcav_camera.raw(evt)
            if img is None: continue

            #logger.info(info_ndarr(img, '  img:'))

            accumulator_xtcav += img 
            n += 1
                
            if n % 5 == 0:
                sys.stdout.write('\r%.1f %% done, %d / %d' % (float(n) / self.parameters.max_shots*100, n, self.parameters.max_shots))
                sys.stdout.flush()   
            if n >= self.parameters.max_shots:
                break                          


        #At the end of the program the total accumulator is saved 
        sys.stdout.write('\nMaximum number of images processed\n') 
        self.image=accumulator_xtcav/n
        self.ROI=roi_xtcav
        
        if not self.parameters.validity_range or not type(self.parameters.validity_range) == tuple:
            self.parameters = self.parameters._replace(validity_range=(self.parameters.run_number, 9999)) #'end'))
        elif len(self.parameters.validity_range) == 1:
            self.parameters = self.parameters._replace(validity_range=(self.parameters.validity_range[0], 9999)) #'end'))

        logger.info(info_ndarr(self.image, 'averaged raw:'))

        logger.info('self.parameters: %s' % str(self.parameters))

        if save_to_file:
            #cp = CalibrationPaths(dataSource.env(), self.parameters.calibration_path)
            #fname = cp.newCalFileName(cons.DB_FILE_NAME, self.parameters.validity_range[0], self.parameters.validity_range[1])
            fname = 'cons-%s-%04d-%s-dark.data' % (run.expt, run.runnum, cons.SRC)

            self.save(fname)

        ###=======================
        #sys.exit('TEST EXIT OK')
        ###=======================
            
    
    @staticmethod
    def _getCalibrationValues(run, xtcav_camera, start_image):
        roi_xtcav = None
        end_of_images = 1e6 # len(times)

        for nev,evt in enumerate(run.events()):
            logger.info('C-loop event %03d'%nev)
            img = xtcav_camera.raw(evt)
            if img is None: continue
            #logger.info(info_ndarr(img, '  img:'))
            roi_xtcav = xtup.getXTCAVImageROI(evt)
            if not roi_xtcav : continue
            return roi_xtcav, nev

        sys.exit('ABORT : _getCalibrationValues detector configuration is not available in the dataset')
        return roi_xtcav, end_of_images


    def save(self, path): 

        instance = copy.deepcopy(self)

        # LCLS1:
        #if instance.ROI:
        #    instance.ROI = dict(vars(instance.ROI))
        #    instance.parameters = dict(vars(instance.parameters))

        if instance.ROI:
            instance.ROI = dict(instance.ROI._asdict())
            instance.parameters = dict(instance.parameters._asdict())
            #logger.debug('XXX instance.ROI:\n%s' % str(instance.ROI))
            #logger.debug('XXX instance.parameters:\n%s' % str(instance.parameters))
            logger.debug('XXX instance.__dict__:\n%s' % str(instance.__dict__))

        constSave(instance, path)

        logger.info('%s\n\t    Saved file: %s' % (50*'_', path))
        logger.info('command to check file: hdf5explorer %s' % path)

        d = instance.parameters
        s = 'cdb add -e %s -d %s -c pedestals -r %s -f %s -i txt -u <user>' % (d['experiment'], cons.SRC, d['run_number'], path)
        logger.info('command to deploy: %s' % s)


    @staticmethod    
    def load(path):        
        obj = constLoad(path)
        try:
            obj.ROI = ROIMetrics(**obj.ROI)
            obj.parameters = DarkBackgroundParameters(**obj.parameters)
        except (AttributeError, TypeError):
            logger.info("Could not load Dark Reference with path "+ path+". Try recreating dark reference " +\
            "to ensure compatability between versions")
            return None
        return obj

DarkBackgroundParameters = namedtuple('DarkBackgroundParameters', 
    ['experiment', 
     'max_shots', 
     'run_number', 
     'validity_range', 
     'calibration_path'])

#----------
#----------
#----------