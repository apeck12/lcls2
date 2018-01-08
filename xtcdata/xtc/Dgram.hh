#ifndef XtcData_Dgram_hh
#define XtcData_Dgram_hh

#include "Sequence.hh"
#include "Xtc.hh"
#include <stdint.h>

#pragma pack(push,4)

namespace XtcData
{

class Transition {
public:
    Sequence seq;
    unsigned evtcounter:24;
    unsigned version:8;
    uint64_t env;
};

class L1Transition : public Transition {
public:
    uint16_t trigLines()     { return (env>>16)&0xffff; }
    uint16_t readoutGroups() { return (env)&0xffff; }
};

class Dgram : public Transition {
public:
    Xtc xtc;
};

}

#pragma pack(pop)

#endif
