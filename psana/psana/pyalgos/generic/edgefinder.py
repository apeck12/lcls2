import numpy as np
import json

class EdgeFinder(object):

    def __init__(self, calibconst):
        self.calibconst = calibconst
        self.kernel = self._get_fir_coefficients() 
        self.delayed_denominator, _ = self.calibconst['delayed_denom']

    def __call__(self, image, parsed_frame_object, IIR):
        firmware_edge = None
        software_edge = None

        #known good length values that a raw frame can take
        assert image.shape == (2208,) or image.shape == (144,) or image.shape == (4272,)
        
        #make sure the edge position falls within the pixel range
        assert parsed_frame_object.edge_position >=0 and parsed_frame_object.edge_position <= 2047
        
        #validating that we're getting the correct number of pixels
        if(parsed_frame_object.prescaled_frame is not None):
            assert len(parsed_frame_object.prescaled_frame) == 2048

            firmware_edge = parsed_frame_object.edge_position

            # check if IIR exists 
            if IIR is None:
                IIR = np.zeros(parsed_frame_object.prescaled_frame.shape, dtype=parsed_frame_object.prescaled_frame.dtype)
            software_edge = np.argmax( np.convolve( \
                (parsed_frame_object.prescaled_frame-IIR)/ self.delayed_denominator, \
                self.kernel) )

        if(parsed_frame_object.background_frame is not None):
            #validating that we're getting the correct number of pixels from the firmware
            assert len(parsed_frame_object.background_frame) == 2048
        
        return firmware_edge, software_edge

    def _twos_complement(self, hexstr,bits):
        value = int(hexstr,16)
        if value & (1 << (bits-1)):
            value -= 1 << bits
        return value

    def _get_fir_coefficients(self):
        my_kernel = []
        my_dict_str, _ = self.calibconst['fir_coefficients']
        my_dict = json.loads(my_dict_str.replace("'", '"'))
        for key, val in my_dict.items():
            mystring = my_dict[key]
            my_kernel.extend([self._twos_complement(mystring[i:i+2],8) for i in range(0,len(mystring),2)])
        return my_kernel
    