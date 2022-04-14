#ifndef Pds_Eb_EventBuilder_hh
#define Pds_Eb_EventBuilder_hh

#include <stdint.h>
#include <vector>

#include "psdaq/service/LinkedList.hh"
#include "psdaq/service/GenericPool.hh"
#include "psdaq/service/fast_monotonic_clock.hh"

namespace Pds {
  class EbDgram;
};

namespace Pds {

  namespace Eb {

    class EbEpoch;
    class EbEvent;

    class EventBuilder
    {
    public:
      EventBuilder(unsigned        epochs,
                   unsigned        entries,
                   unsigned        sources,
                   uint64_t        mask,
                   unsigned        timeout,
                   const unsigned& verbose);
      virtual ~EventBuilder();
    public:
      virtual void       flush() {}
      virtual void       fixup(EbEvent*, unsigned srcId)     = 0;
      virtual void       process(EbEvent*)                   = 0;
      virtual uint64_t   contract(const Pds::EbDgram*) const = 0;
    public:
      void               expired();
    public:
      void               process(const Pds::EbDgram* dgrams,
                                 const size_t        bufSize,
                                 unsigned            prm);
    public:
      void               resetCounters();
      void               clear();
      void               dump(unsigned detail) const;
      const uint64_t&    epochAllocCnt()  const;
      const uint64_t&    epochFreeCnt()   const;
      //const uint64_t&    epochOccCnt()    const;
      const uint64_t&    eventAllocCnt()  const;
      const uint64_t&    eventFreeCnt()   const;
      const uint64_t&    eventOccCnt()    const;
      const uint64_t     eventPoolDepth() const; // Right: not a ref
      const uint64_t&    timeoutCnt()     const;
      const uint64_t&    fixupCnt()       const;
      const uint64_t&    missing()        const;
      const uint64_t&    eventAge()       const;
    private:
      friend class EbEvent;
      using time_point_t = std::chrono::time_point<fast_monotonic_clock>;
      using ns_t         = std::chrono::nanoseconds;
    private:
      unsigned          _epIndex(uint64_t key) const;
      unsigned          _evIndex(uint64_t key) const;
    private:
      EbEpoch*          _match(uint64_t key);
      EbEpoch*          _epoch(uint64_t key, EbEpoch* after);
      void              _flushBefore(EbEpoch*);
      EbEpoch*          _discard(EbEpoch*);
      void              _fixup(EbEvent*, ns_t age, const EbEvent* const due);
      EbEvent*          _event(const Pds::EbDgram*, EbEvent* after, unsigned prm);
      void              _flush(const EbEvent* const due);
      void              _flush();
      void              _tryFlush();
      void              _retire(EbEvent*);
      EbEvent*          _insert(EbEpoch*, const Pds::EbDgram*, EbEvent*, unsigned prm);
    private:
      LinkedList<EbEpoch>   _pending;       // Listhead, Epochs with events pending
      time_point_t          _tLastFlush;    // Starting time of timeout
      const uint64_t        _mask;          // Sequence mask
      GenericPool           _epochFreelist; // Freelist for new epochs
      std::vector<EbEpoch*> _epochLut;      // LUT of allocated epochs
      GenericPool           _eventFreelist; // Freelist for new events
      std::vector<EbEvent*> _eventLut;      // LUT of allocated events
      const ns_t            _eventTimeout;  // Maximum event age in ms
      mutable uint64_t      _tmoEvtCnt;     // Count of timed out events
      mutable uint64_t      _fixupCnt;      // Count of flushed   events
      mutable uint64_t      _missing;       // Bit list of missing contributors
      //mutable uint64_t      _epochOccCnt;   // Number of epochs in use
      mutable uint64_t      _eventOccCnt;   // Number of events in use
      mutable uint64_t      _age;           // Event age
      const unsigned&       _verbose;       // Print progress info
    };
  };
};

inline const uint64_t& Pds::Eb::EventBuilder::epochAllocCnt() const
{
  return _epochFreelist.numberofAllocs();
}

inline const uint64_t& Pds::Eb::EventBuilder::epochFreeCnt() const
{
  return _epochFreelist.numberofFrees();
}

// Revisit: This one is not terribly interesting and mirrors eventOccCnt()
//inline const uint64_t& Pds::Eb::EventBuilder::epochOccCnt() const
//{
//  _epochOccCnt = _epochFreelist.numberofAllocs() - _epochFreelist.numberofFrees();
//
//  return _epochOccCnt;
//}

inline const uint64_t& Pds::Eb::EventBuilder::eventAllocCnt() const
{
  return _eventFreelist.numberofAllocs();
}

inline const uint64_t& Pds::Eb::EventBuilder::eventFreeCnt() const
{
  return _eventFreelist.numberofFrees();
}

inline const uint64_t& Pds::Eb::EventBuilder::eventOccCnt() const
{
  _eventOccCnt = _eventFreelist.numberofAllocs() - _eventFreelist.numberofFrees();

  return _eventOccCnt;
}

inline const uint64_t Pds::Eb::EventBuilder::eventPoolDepth() const
{
  // Return a copy of the value instead of a reference
  // since it is nominally called only once by MetricExporter
  return _eventFreelist.numberofObjects();
}

inline const uint64_t& Pds::Eb::EventBuilder::timeoutCnt() const
{
  return _tmoEvtCnt;
}

inline const uint64_t& Pds::Eb::EventBuilder::fixupCnt() const
{
  return _fixupCnt;
}

inline const uint64_t& Pds::Eb::EventBuilder::missing() const
{
  return _missing;
}

inline const uint64_t& Pds::Eb::EventBuilder::eventAge() const
{
  return _age;
}

#endif
