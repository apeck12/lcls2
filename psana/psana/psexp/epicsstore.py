from psana.dgram import Dgram
from psana.event import Event
from psana.psexp.packet_footer import PacketFooter
import numpy as np
from collections import defaultdict

class Epics(object):
    """ Store list of Epics dgrams, timestatmps, and variables """
    
    def __init__(self, config):
        self.config = config
        self.dgrams = []
        self.timestamps = []
        self.buf = bytearray() # keeps remaining data of each Epics file
        self.offset = 0
        self.n_items = 0
        self._init_epics_variables()

    def _init_epics_variables(self):
        """ From the given config, build a list of keywords from
        config.software.xppepics.[alg:fast/slow].[] fields."""
        algs = vars(self.config.xppepics[0])
        self.epics_variables = {}
        for alg in algs:
            self.epics_variables[alg] = list(eval("vars(self.config.software.xppepics.%s)"%alg))

    def add(self, d):
        self.dgrams.append(d)
        self.timestamps.append(d.seq.timestamp())
        self.n_items += 1

class EpicsStore(object):
    """ Manages Epics data 
    Takes list of memoryviews Epics data and updates the store."""

    def __init__(self, configs):
        """ Builds store with the given epics config."""
        self.n_files = 0
        self._epics_list = []
        self.epics_variables = defaultdict(list)
        if configs:
            self.n_files = len(configs)
            self._epics_list = [Epics(config) for config in configs]

            # Collects epics variables from all epics files
            for epics in self._epics_list:
                for key, val in epics.epics_variables.items(): 
                    self.epics_variables[key] += val
    
    def alg_from_variable(self, variable_name):
        """ Returns algorithm name from the given epics variable. """
        for key, val in self.epics_variables.items():
            if variable_name in val:
                return key
        return None

    def update(self, views):
        """ Updates the store with new data from list of views. """
        if views:
            for i in range(self.n_files):
                view, epics = views[i], self._epics_list[i]
                mmr_view = memoryview(epics.buf + view)
                while epics.offset < mmr_view.shape[0]:
                    d = Dgram(view=mmr_view, config=epics.config, offset=epics.offset)
                    
                    # check if this is a broken dgram (not enough data in buffer)
                    if epics.offset + d._size > mmr_view.shape[0]:
                        break
                    
                    epics.add(d)
                    epics.offset += d._size
                
                if epics.offset < mmr_view.shape[0]:
                    epics.buf = mmr_view[epics.offset:].tobytes() # copy remaining data to the beginning of buffer

    def _checkout(self, event_timestamps):
        """ Builds an epics dictionary using data from all epics files
        with matching timestamps."""
        if not self.n_files:
            return None
        
        epics_dicts = [dict() for i in range(self.n_files)] # keeps key-val for each event
        for epics in self._epics_list:
            found_pos = np.searchsorted(epics.timestamps, event_timestamps)
        
            # Returns last epics event for all newer events
            found_pos[found_pos == epics.n_items] = epics.n_items - 1
            for i, pos in enumerate(found_pos):
                algs = vars(epics.config.xppepics[0])
                for alg in algs:
                    if alg in vars(epics.dgrams[pos].xppepics[0]):
                        epics_dicts[i].update(eval("vars(epics.dgrams[%d].xppepics[0].%s)"%(pos, alg)))
        
        return epics_dicts

    def checkout_by_events(self, events):
        """ Returns epics events corresponded to the given bigdata events 
        (use timestamp for matching). """
        event_timestamps = np.asarray([evt._timestamp for evt in events], dtype=np.uint64)
        return self._checkout(event_timestamps)

    def checkout_by_timestamps(self, event_timestamps):
        """ Returns epics events matched with the given list of timstamps."""
        return self._checkout(event_timestamps)

