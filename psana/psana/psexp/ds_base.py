import weakref
import os
import glob
import abc
import numpy as np
import pathlib

from psana.dgrammanager import DgramManager

from psana.psexp import PrometheusManager, SmdReaderManager
import threading
from psana import dgram

from psana.detector import detectors
import psana.pscalib.calib.MDBWebUtils as wu

import logging
logger = logging.getLogger(__name__)

from dataclasses import dataclass

class InvalidDataSourceArgument(Exception): pass

@dataclass
class DsParms:
    batch_size: int
    max_events: int
    filter: int
    destination: int
    prom_man: int
    max_retries: int
    live: bool
    found_xtc2_callback: int

    def set_det_class_table(self, det_classes, xtc_info, det_info_table):
        self.det_classes, self.xtc_info, self.det_info_table = det_classes, xtc_info, det_info_table

    def set_use_smds(self, use_smds):
        self.use_smds = use_smds


class DataSourceBase(abc.ABC):
    def __init__(self, **kwargs):
        """Initializes datasource base"""
        self.filter      = 0         # callback that takes an evt and return True/False.
        self.batch_size  = 1000      # no. of events per batch sent to a bigdata core
        self.max_events  = 0         # no. of maximum events
        self.detectors   = []        # user-selected detector names
        self.exp         = None      # experiment id (e.g. xpptut13)
        self.runnum      = None      # run no. 
        self.live        = False     # turns live mode on/off 
        self.dir         = None      # manual entry for path to xtc files
        self.files       = None      # xtc2 file path
        self.shmem       = None
        self.destination = 0         # callback that returns rank no. (used by EventBuilder)
        self.monitor     = False     # turns prometheus monitoring client of/off
        self.small_xtc   = []        # swap smd file(s) with bigdata files for these detetors

        if kwargs is not None:
            self.smalldata_kwargs = {}
            keywords = ('exp', 
                    'dir', 
                    'files', 
                    'shmem', 
                    'filter', 
                    'batch_size', 
                    'max_events', 
                    'detectors', 
                    'det_name',
                    'destination',
                    'live',
                    'smalldata_kwargs', 
                    'monitor',
                    'small_xtc',
                    )
            
            for k in keywords:
                if k in kwargs:
                    setattr(self, k, kwargs[k])

            if self.destination != 0:
                self.batch_size = 1 # reset batch_size to prevent L1 transmitted before BeginRun (FIXME?: Mona)

            if 'run' in kwargs:
                setattr(self, 'runnum', kwargs['run'])

            if 'files' in kwargs:
                if isinstance(self.files, str):
                    self.files = [self.files]

            max_retries = 0
            if self.live: 
                max_retries = int(os.environ.get('PS_R_MAX_RETRIES', '3'))

        assert self.batch_size > 0
        
        self.prom_man = PrometheusManager(os.environ['PS_PROMETHEUS_JOBID'])
        self.dsparms  = DsParms(self.batch_size, 
                self.max_events, 
                self.filter, 
                self.destination, 
                self.prom_man, 
                max_retries, 
                self.live,
                self.found_xtc2_callback) 

    def found_xtc2_callback(self, file_type):
        """ Returns a list of True/False if .xtc2 file is found 
        
        Required file_type is either 'smd' or 'bd'.

        For each xtc file, returns True ONLY IF using .inprogress files and .xtc2 files 
        are found on disk. We return False in the case where .inprogress
        files are not used because this callback is used to check if we
        should wait for more data on the xtc files. The read-retry will not
        happen if this callback returns True and there's 0 byte read out."""
        found_flags = [False] * self.n_files 

        xtc_files = []
        if file_type == 'smd':
            xtc_files = self.smd_files
        elif file_type == 'bd':
            xtc_files = self.xtc_files

        if self.xtc_ext == '.xtc2.inprogress' and xtc_files: 
            found_flags = [os.path.isfile(os.path.splitext(xtc_file)[0]) for xtc_file  in xtc_files] 
        return found_flags
    
    @abc.abstractmethod
    def runs(self):
        return

    @abc.abstractmethod
    def is_mpi(self):
        return

    def unique_user_rank(self):
        """ Only applicable to MPIDataSource
        All other types of DataSource always return True.
        For MPIDataSource, only the last bigdata rank returns True."""
        if not self.is_mpi():
            return True

        n_srv_ranks = int(os.environ.get('PS_SRV_NODES', 0))
        my_rank = self.comms.world_rank
        world_size = self.comms.world_size

        if my_rank == (world_size - n_srv_ranks - 1):
            return True
        else:
            return False

    # to be added at a later date...
    #@abc.abstractmethod
    #def steps(self):
    #    retur
    
    def _setup_run_files(self, runnum):
        """
        Generate list of smd and xtc files given a run number.
        Priority is given to .inprogress files.
        """
        smd_dir = os.path.join(self.xtc_path, 'smalldata')
        all_smd_files = glob.glob(os.path.join(smd_dir, '*r%s-s*.smd.xtc2'%(str(runnum).zfill(4)))) + \
                glob.glob(os.path.join(smd_dir, '*r%s-s*.smd.xtc2.inprogress'%(str(runnum).zfill(4))))
        logger.debug(f'all_smd_files={all_smd_files}')

        smd_files = [smd_file for smd_file in all_smd_files if smd_file.endswith('.inprogress')]

        # No .inprogress files found
        if not smd_files: 
            smd_files = all_smd_files
            self.xtc_ext = '.xtc2'
        else:
            self.xtc_ext = '.xtc2.inprogress' 
            
        xtc_files = [os.path.join(self.xtc_path, \
                     os.path.basename(smd_file).split('.smd')[0] + self.xtc_ext) \
                     for smd_file in smd_files \
                     if os.path.isfile(os.path.join(self.xtc_path, \
                     os.path.basename(smd_file).split('.smd')[0] + self.xtc_ext))]

        # For chunking test, xtc_files are in -cNN format.
        if not xtc_files:
            logger.debug(f'WARNING: looks like bigdata could have been chunked.')
            xtc_files = [os.path.join(self.xtc_path, \
                         os.path.basename(smd_file).split('.smd')[0] + '-c00' + self.xtc_ext) \
                         for smd_file in smd_files \
                         if os.path.isfile(os.path.join(self.xtc_path, \
                         os.path.basename(smd_file).split('.smd')[0] + '-c00' + self.xtc_ext))]

        self.smd_files = smd_files
        self.xtc_files = xtc_files
        self.n_files   = len(self.smd_files)

        logger.debug(f'smd_files={smd_files}')
        logger.debug(f'xtc_files={xtc_files}')
        self.dsparms.set_use_smds([False] * self.n_files)

    def _setup_runnum_list(self):
        """
        Requires:
        1) exp
        2) exp, run=N or [N, M, ...]
        
        Build runnum_list for the experiment.
        """

        if not self.exp:
            raise InvalidDataSourceArgument("Missing exp or files input")
            
        if self.dir:
            self.xtc_path = self.dir
        else:
            xtc_dir = os.environ.get('SIT_PSDM_DATA', '/reg/d/psdm')
            self.xtc_path = os.path.join(xtc_dir, self.exp[:3], self.exp, 'xtc')
        
        if self.runnum is None:
            run_list = [int(os.path.splitext(os.path.basename(_dummy))[0].split('-r')[1].split('-')[0]) \
                    for _dummy in glob.glob(os.path.join(self.xtc_path, '*-r*.xtc2'))]
            assert run_list
            run_list = list(set(run_list))
            run_list.sort()
            self.runnum_list = run_list
        elif isinstance(self.runnum, list):
            self.runnum_list = self.runnum
        elif isinstance(self.runnum, int):
            self.runnum_list = [self.runnum]
        else:
            raise InvalidDataSourceArgument("run accepts only int or list. Leave out run arugment to process all available runs.")

    def smalldata(self, **kwargs):
        self.smalldata_obj.setup_parms(**kwargs)
        return self.smalldata_obj

    def _start_prometheus_client(self, mpi_rank=0):
        if not self.monitor:
            logger.debug('ds_base: RUN W/O PROMETHEUS CLENT')
        else:
            logger.debug('ds_base: START PROMETHEUS CLIENT (JOBID:%s RANK: %d)'%(self.prom_man.jobid, mpi_rank))
            self.e = threading.Event()
            self.t = threading.Thread(name='PrometheusThread%s'%(mpi_rank),
                    target=self.prom_man.push_metrics,
                    args=(self.e, mpi_rank),
                    daemon=True)
            self.t.start()

    def _end_prometheus_client(self, mpi_rank=0):
        if not self.monitor:
            return

        logger.debug('ds_base: END PROMETHEUS CLIENT (JOBID:%s RANK: %d)'%(self.prom_man.jobid, mpi_rank))
        self.e.set()

    def _apply_detector_selection(self):
        """
        Handles two arguments
        1) detectors=[detname,]
            Reduce no. of smd/xtc files to only those with selectec detectors
        2) small_xtc=[detname,]
            Swap out smd files with these given detectors with bigdata files
        """
        use_smds = [False] * len(self.smd_files)
        if self.detectors or self.small_xtc:
            # Get tmp configs using SmdReader
            # this smd_fds, configs, and SmdReader will not be used later
            smd_fds  = np.array([os.open(smd_file, os.O_RDONLY) for smd_file in self.smd_files], dtype=np.int32)
            logger.debug(f'ds_base: smd0 opened tmp smd_fds: {smd_fds}')
            smdr_man = SmdReaderManager(smd_fds, self.dsparms)
            all_configs = smdr_man.get_next_dgrams()
            
            xtc_files = []
            smd_files = []
            configs = []
            if self.detectors:
                s1 = set(self.detectors)
                for i, config in enumerate(all_configs):
                    if s1.intersection(set(config.software.__dict__.keys())):
                        if self.xtc_files: xtc_files.append(self.xtc_files[i])
                        if self.smd_files: smd_files.append(self.smd_files[i])
                        configs.append(config)
                msg = f"""ds_base: applying detector selection
    selected detectors: {self.detectors}
    smd_files n_selected/n_total: {len(smd_files)}/{len(self.smd_files)}
    {','.join([os.path.basename(smd_file) for smd_file in smd_files])}
    xtc_files n_selected/n_total: {len(xtc_files)}/{len(self.xtc_files)}
    {','.join([os.path.basename(xtc_file) for xtc_file in xtc_files])}
    """
                logger.debug(msg)
            else:
                xtc_files = self.xtc_files[:]
                smd_files = self.smd_files[:]
                configs = all_configs

            use_smds = [False] * len(smd_files)
            if self.small_xtc:
                s1 = set(self.small_xtc)
                msg = f"""ds_base: applying smd files swap
    selected detectors: {self.small_xtc}\n"""
                for i, config in enumerate(configs):
                    if s1.intersection(set(config.software.__dict__.keys())):
                        smd_files[i] = xtc_files[i]
                        use_smds[i] = True
                    msg += f'   smd_files[{i}]={os.path.basename(smd_files[i])} use_smds[{i}]={use_smds[i]}\n'
                logger.debug(msg)
            
            self.xtc_files = xtc_files
            self.smd_files = smd_files
            
            for smd_fd in smd_fds:
                os.close(smd_fd)
            logger.debug(f"ds_base: close tmp smd fds:{smd_fds}")

        self.dsparms.set_use_smds(use_smds)

    def _setup_det_class_table(self):
        """
        this function gets the version number for a (det, drp_class) combo
        maps (dettype,software,version) to associated python class and
        detector info for a det_name maps to dettype, detid tuple.
        """
        det_classes = {'epics': {}, 'scan': {}, 'step': {}, 'normal': {}}

        xtc_info = []
        det_info_table = {}

        # loop over the dgrams in the configuration
        # if a detector/drp_class combo exists in two cfg dgrams
        # it will be OK... they should give the same final Detector class

        for i, cfg_dgram in enumerate(self._configs):
            for det_name, det_dict in cfg_dgram.software.__dict__.items():
                # go find the class of the first segment in the dict
                # they should all be identical
                first_key = next(iter(det_dict.keys()))
                det = det_dict[first_key]

                if det_name not in det_classes:
                    det_class_table = det_classes['normal']
                else:
                    det_class_table = det_classes[det_name]


                dettype, detid = (None, None)
                for drp_class_name, drp_class in det.__dict__.items():

                    # collect detname maps to dettype and detid
                    if drp_class_name == 'dettype':
                        dettype = drp_class
                        continue

                    if drp_class_name == 'detid':
                        detid = drp_class
                        continue

                    # FIXME: we want to skip '_'-prefixed drp_classes
                    #        but this needs to be fixed upstream
                    if drp_class_name.startswith('_'): continue

                    # use this info to look up the desired Detector class
                    versionstring = [str(v) for v in drp_class.version]
                    class_name = '_'.join([det.dettype, drp_class.software] + versionstring)
                    xtc_entry = (det_name,det.dettype,drp_class_name,'_'.join(versionstring))
                    if xtc_entry not in xtc_info:
                        xtc_info.append(xtc_entry)
                    if hasattr(detectors, class_name):
                        DetectorClass = getattr(detectors, class_name) # return the class object
                        det_class_table[(det_name, drp_class_name)] = DetectorClass
                    else:
                        pass

                det_info_table[det_name] = (dettype, detid)

        self.dsparms.set_det_class_table(det_classes, xtc_info, det_info_table)

    def _set_configinfo(self):
        """ From configs, we generate a dictionary lookup with det_name as a key.
        The information stored the value field contains:
        
        - configs specific to that detector
        - sorted_segment_ids
          used by Detector cls for checking if an event has correct no. of segments
        - detid_dict
          has segment_id as a key
        - dettype
        - uniqueid
        """
        self.dsparms.configinfo_dict = {}

        for detcls_name, det_class in self.dsparms.det_classes.items(): # det_class is either normal or envstore ('epics', 'scan', 'step')
            for (det_name, _), _ in det_class.items():
                # we lose a "one-to-one" correspondence with event dgrams.  we may have
                # to put in None placeholders at some point? - mona and cpo
                det_configs = [cfg for cfg in self._configs if hasattr(cfg.software, det_name)]
                sorted_segment_ids = []
                # a dictionary of the ids (a.k.a. serial-number) of each segment
                detid_dict = {}
                dettype = ""
                uniqueid = ""
                for config in det_configs:
                    seg_dict = getattr(config.software, det_name)
                    sorted_segment_ids += list(seg_dict.keys())
                    for segment, det in seg_dict.items():
                        detid_dict[segment] = det.detid
                        dettype = det.dettype
                
                sorted_segment_ids.sort()
                
                uniqueid = dettype
                for segid in sorted_segment_ids:
                    uniqueid += '_'+detid_dict[segid]

                self.dsparms.configinfo_dict[det_name] = type("ConfigInfo", (), {\
                        "configs": det_configs, \
                        "sorted_segment_ids": sorted_segment_ids, \
                        "detid_dict": detid_dict, \
                        "dettype": dettype, \
                        "uniqueid": uniqueid})
    
    def _setup_run_calibconst(self):
        """
        note: calibconst is set differently in RunParallel (see node.py: BigDataNode)
        """
        runinfo = self._get_runinfo()
        if not runinfo:
            return
        expt, runnum, _ = runinfo
        
        self.dsparms.calibconst = {}
        for det_name, configinfo in self.dsparms.configinfo_dict.items():
            if expt: 
                if expt == "cxid9114": # mona: hack for cctbx
                    det_uniqueid = "cspad_0002"
                elif expt == "xpptut15":
                    det_uniqueid = "cspad_detnum1234"
                else:
                    det_uniqueid = configinfo.uniqueid
                calib_const = wu.calib_constants_all_types(det_uniqueid, exp=expt, run=runnum)
                
                # mona - hopefully this will be removed once the calibconst
                # db all use uniqueid as an identifier
                if not calib_const:
                    calib_const = wu.calib_constants_all_types(det_name, exp=expt, run=runnum)
                self.dsparms.calibconst[det_name] = calib_const
            else:
                print(f"ds_base: Warning: cannot access calibration constant (exp is None)")
                self.dsparms.calibconst[det_name] = None
    
    def _get_runinfo(self):
        if not self.beginruns : return

        beginrun_dgram = self.beginruns[0]
        if hasattr(beginrun_dgram, 'runinfo'): # some xtc2 do not have BeginRun
            expt = beginrun_dgram.runinfo[0].runinfo.expt 
            runnum = beginrun_dgram.runinfo[0].runinfo.runnum
            timestamp = beginrun_dgram.timestamp()
            return expt, runnum, timestamp

    def _close_opened_smd_files(self):
        # Make sure to close all smd files opened by previous run
        if self.smd_fds is not None:
            for fd in self.smd_fds:
                os.close(fd)
            logger.debug(f'ds_base: close smd fds: {self.smd_fds}')


