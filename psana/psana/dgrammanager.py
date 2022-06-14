import sys, os
import time
import getopt
import pprint

try:
    # doesn't exist on macos
    from shmem import PyShmemClient
except:
    pass
from psana import dgram
from psana.event import Event
from psana.detector import detectors
from psana.psexp.event_manager import TransitionId
import numpy as np

def dumpDict(dict,indent):
    for k in sorted(dict.keys()):
        if hasattr(dict[k],'__dict__'):
            print(' '*indent,k)
            dumpDict(dict[k].__dict__,indent+2)
        else:
            print(' '*indent,k,dict[k])

# method to dump dgrams to stdout.  ideally this would move into dgram.cc
def dumpDgram(d):
    dumpDict(d.__dict__,0)

FN_L = 200

# Warning: If XtcData::Dgram ever changes, this function will likely need to change
def _service(view):
    iSvc = 2                    # Index of service field, in units of uint32_t
    return (np.array(view, copy=False).view(dtype=np.uint32)[iSvc] >> 24) & 0x0f

# Warning: If XtcData::Dgram ever changes, this function will likely need to change
def _dgSize(view):
    iExt = 5                    # Index of extent field, in units of uint32_t
    txSize = 3 * 4              # sizeof(XtcData::TransitionBase)
    return txSize + np.array(view, copy=False).view(dtype=np.uint32)[iExt]

class DgramManager(object):

    def __init__(self, xtc_files, configs=[], fds=[], 
            tag=None, run=None, max_retries=0):
        """ Opens xtc_files and stores configs.
        If file descriptors (fds) is given, reuse the given file descriptors.
        """
        self.xtc_files = []
        self.shmem_cli = None
        self.shmem_kwargs = {'index':-1,'size':0,'cli_cptr':None}
        self.configs = []
        self._timestamps = [] # built when iterating
        self._run = run
        self.found_endrun = True
        self.buffered_beginruns = []
        self.max_retries = max_retries
        self.chunk_ids = []
        
        if isinstance(xtc_files, (str)):
            self.xtc_files = np.array([xtc_files], dtype='U%s'%FN_L)
        elif isinstance(xtc_files, (list, np.ndarray)):
            if len(xtc_files) > 0: # handles smalldata-only case
                if xtc_files[0] == 'shmem':
                    self.shmem_cli = PyShmemClient()
                    #establish connection to available server - blocking
                    status = int(self.shmem_cli.connect(tag,0))
                    assert not status,'shmem connect failure %d' % status
                    #wait for first configure datagram - blocking
                    view = self.shmem_cli.get(self.shmem_kwargs)
                    assert view
                    # Release shmem buffer after copying Transition data
                    # cpo: copy L1Accepts too because some shmem
                    # applications like AMI's pickN can hold references
                    # to dgrams for a long time, consuming the shmem buffers
                    # and creating a deadlock situation. could revisit this
                    # later and only deep-copy arrays inside pickN, for example
                    # but would be more fragile.
                    barray = bytes(view[:_dgSize(view)])
                    self.shmem_cli.freeByIndex(self.shmem_kwargs['index'], self.shmem_kwargs['size'])
                    view = memoryview(barray)
                    d = dgram.Dgram(view=view)
                    self.configs += [d]
                else:
                    self.xtc_files = np.asarray(xtc_files, dtype='U%s'%FN_L)


        self.given_fds = True if len(fds) > 0 else False
        if self.given_fds:
            self.fds = np.asarray(fds, dtype=np.int32)
        else:
            self.fds = np.array([os.open(xtc_file, os.O_RDONLY) for xtc_file in self.xtc_files], dtype=np.int32)

        self.fds_map = {}
        for fd, xtc_file in zip(self.fds, self.xtc_files):
            self.fds_map[fd] = xtc_file
        
        given_configs = True if len(configs) > 0 else False
        if given_configs:
            self.configs = configs
        elif xtc_files[0] != 'shmem':
            self.configs = [dgram.Dgram(file_descriptor=fd, max_retries=self.max_retries) for fd in self.fds]

        self.calibconst = {} # initialize to empty dict - will be populated by run class
        self.n_files = len(self.xtc_files)
        self.set_chunk_ids()

    def set_chunk_ids(self):
        """ Generates a list of chunk ids for all stream files

        Chunk Id is extracted from data file name (e.g. xpptut15-r0001-s000-c000[.smd].xtc2).
        This is a string of length three after -c converted to int.
        """
        if len(self.xtc_files) == 0: return
        if self.xtc_files[0] == 'shmem': return
        for xtc_file in self.xtc_files:
            filename = os.path.basename(xtc_file)
            found = filename.find('-c')  
            if found >= 0:
                self.chunk_ids.append(int(filename[found+2:found+5]))
    
    def get_chunk_id(self, ind):
        if not self.chunk_ids: return None
        return self.chunk_ids[ind]

    def set_chunk_id(self, ind, new_chunk_id):
        self.chunk_ids[ind] = new_chunk_id

    def close(self):
        if not self.given_fds:
            for fd in self.fds:
                os.close(fd)

    def __iter__(self):
        return self

    def _check_missing_endrun(self, beginruns=None):
        fake_endruns = None
        if not self.found_endrun: # there's no previous EndRun
            sec = (self._timestamps[-1] >> 32) & 0xffffffff
            usec = int((self._timestamps[-1] & 0xffffffff) * 1e3 + 1)
            if beginruns:
                self.buffered_beginruns = [dgram.Dgram(config=config,
                        view=d, offset=0, size=d._size)      
                        for d, config in zip(beginruns, self.configs)]
            fake_endruns = [dgram.Dgram(config=config, fake_endrun=1, \
                    fake_endrun_sec=sec, fake_endrun_usec=usec) \
                    for config in self.configs]
            self.found_endrun = True
        else:
            self.found_endrun = False
        return fake_endruns

    def __next__(self):
        """ only support sequential read - no event building"""
        if self.buffered_beginruns:
            self.found_endrun = False
            evt = Event(self.buffered_beginruns, run=self.run())
            self._timestamps += [evt.timestamp]
            self.buffered_beginruns = []
            return evt

        if self.shmem_cli:
            view = self.shmem_cli.get(self.shmem_kwargs)
            if view:
                # Release shmem buffer after copying Transition data
                # cpo: copy L1Accepts too because some shmem
                # applications like AMI's pickN can hold references
                # to dgrams for a long time, consuming the shmem buffers
                # and creating a deadlock situation. could revisit this
                # later and only deep-copy arrays inside pickN, for example
                # but would be more fragile.
                barray = bytes(view[:_dgSize(view)])
                self.shmem_cli.freeByIndex(self.shmem_kwargs['index'], self.shmem_kwargs['size'])
                view = memoryview(barray)
                # use the most recent configure datagram
                config = self.configs[len(self.configs)-1]
                d = dgram.Dgram(config=config,view=view)
                dgrams = [d]
            else:
                raise StopIteration
        else:
            try:
                dgrams = [dgram.Dgram(config=config, max_retries=self.max_retries) for config in self.configs]
            except StopIteration as err:
                fake_endruns = self._check_missing_endrun()
                if fake_endruns:
                    dgrams = fake_endruns
                else:
                    print(err)
                    raise StopIteration
                

        # Check BeginRun - EndRun pairing
        service = dgrams[0].service()
        if service == TransitionId.BeginRun:
            fake_endruns = self._check_missing_endrun(beginruns=dgrams)
            if fake_endruns:
                dgrams = fake_endruns
        
        if service == TransitionId.EndRun:
            self.found_endrun = True

        evt = Event(dgrams, run=self.get_run())
        self._timestamps += [evt.timestamp]
        return evt

    def jumps(self, dgram_i, offset, size):
        if offset == 0 and size == 0:
            d = None
        else:
            try:
                d = dgram.Dgram(file_descriptor=self.fds[dgram_i], 
                    config=self.configs[dgram_i], 
                    offset=offset, 
                    size=size, 
                    max_retries=self.max_retries)
            except StopIteration:
                d = None
        return d

    def jump(self, offsets, sizes):
        """ Jumps to the offset and reads out dgram on each xtc file.
        This is used in normal mode (multiple detectors with MPI).
        """
        assert len(offsets) > 0 and len(sizes) > 0
        dgrams = [self.jumps(dgram_i, offset, size) for dgram_i, (offset, size)
            in enumerate(zip(offsets, sizes))]
        evt = Event(dgrams, run=self._run)
        return evt

    def get_timestamps(self):
        return np.asarray(self._timestamps, dtype=np.uint64) # return numpy array for easy search later

    def set_run(self, run):
        self._run = run
    
    def get_run(self):
        return self._run

def parse_command_line():
    opts, args_proper = getopt.getopt(sys.argv[1:], 'hvd:f:')
    xtcdata_filename="data.xtc"
    for option, parameter in opts:
        if option=='-h': usage_error()
        if option=='-f': xtcdata_filename = parameter
    if xtcdata_filename is None:
        xtcdata_filename="data.xtc"
    return (args_proper, xtcdata_filename)

def getMemUsage():
    pid=os.getpid()
    ppid=os.getppid()
    cmd="/usr/bin/ps -q %d --no-headers -eo size" % pid
    p=os.popen(cmd)
    size=int(p.read())
    return size

def main():
    args_proper, xtcdata_filename = parse_command_line()
    ds=DgramManager(xtcdata_filename)
    print("vars(ds):")
    for var_name in sorted(vars(ds)):
        print("  %s:" % var_name)
        e=getattr(ds, var_name)
        if not isinstance(e, (tuple, list, int, float, str)):
            for key in sorted(e.__dict__.keys()):
                print("%s: %s" % (key, e.__dict__[key]))
    print()
    count=0
    for evt in ds:
        print("evt:", count)
        for dgram in evt:
            for var_name in sorted(vars(dgram)):
                val=getattr(dgram, var_name)
                print("  %s: %s" % (var_name, type(val)))
            a=dgram.xpphsd.raw.array0Pgp
            try:
                a[0][0]=999
            except ValueError:
                print("The dgram.xpphsd.raw.array0Pgp is read-only, as it should be.")
            else:
                print("Warning: the evt.array0_pgp array is writable")
            print()
        count+=1
    return

def usage_error():
    s="usage: python %s" %  os.path.basename(sys.argv[0])
    sys.stdout.write("%s [-h]\n" % s)
    sys.stdout.write("%s [-f xtcdata_filename]\n" % (" "*len(s)))
    sys.exit(1)

if __name__=='__main__':
    main()

