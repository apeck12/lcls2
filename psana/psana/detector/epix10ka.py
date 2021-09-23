"""Data access VERSIONS
   for composite detectors made of epix10ka segments/panels.
"""
import numpy as np
from amitypes import Array2d
from psana.detector.epix_base import epix_base, logging
logger = logging.getLogger(__name__)

class epix10k_raw_0_0_1(epix_base):
    def __init__(self, *args, **kwargs):
        logger.debug('epix10k_raw_0_0_1.__init__')
        epix_base.__init__(self, *args, **kwargs)


class epix10ka_raw_2_0_1(epix_base):
    def __init__(self, *args, **kwargs):
        epix_base.__init__(self, *args, **kwargs)
    def _array(self, evt) -> Array2d:
        f = None
        segs = self._segments(evt)
        if segs is None:
            pass
        else:
            nsegs = len(segs)
            if nsegs==4:
                nx = segs[0].raw.shape[1]
                ny = segs[0].raw.shape[0]
                f = np.zeros((ny*2,nx*2), dtype=segs[0].raw.dtype)
                xa = [nx, 0, nx, 0]
                ya = [ny, ny, 0, 0]
                for i in range(4):
                    x = xa[i]
                    y = ya[i]
                    f[y:y+ny,x:x+nx] = segs[i].raw & 0x3fff
        return f

#  Old detType for epix10ka
epix_raw_2_0_1 = epix10ka_raw_2_0_1

