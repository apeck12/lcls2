#include "EbAppBase.hh"

#include "Endpoint.hh"
#include "EbEvent.hh"

#include "EbLfServer.hh"

#include "utilities.hh"

#include "psalg/utils/SysLog.hh"
#include "xtcdata/xtc/Dgram.hh"

#ifndef _GNU_SOURCE
#  define _GNU_SOURCE
#endif
#include <sched.h>
#include <signal.h>
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <time.h>
#include <inttypes.h>
#include <climits>
#include <bitset>
#include <atomic>
#include <thread>
#include <chrono>                       // Revisit: Temporary?

using namespace XtcData;
using namespace Pds;
using namespace Pds::Fabrics;
using namespace Pds::Eb;
using logging          = psalg::SysLog;
using MetricExporter_t = std::shared_ptr<MetricExporter>;
using ms_t             = std::chrono::milliseconds;


EbAppBase::EbAppBase(const EbParams&         prms,
                     const MetricExporter_t& exporter,
                     const std::string&      pfx,
                     const uint64_t          duration,
                     const unsigned          maxEntries,
                     const unsigned          maxEvBuffers,
                     const unsigned          maxTrBuffers,
                     const unsigned          msTimeout) :
  EventBuilder (maxEvBuffers + maxTrBuffers,
                maxEntries,
                MAX_DRPS, //Revisit: std::bitset<64>(prms.contributors).count(),
                duration,
                msTimeout,
                prms.verbose),
  _transport   (prms.verbose, prms.kwargs),
  _maxEntries  (maxEntries),
  _maxEvBuffers(maxEvBuffers),
  _maxTrBuffers(maxTrBuffers),
  _verbose     (prms.verbose),
  _bufferCnt   (0),
  _contributors(0),
  _id          (-1),
  _exporter    (exporter),
  _pfx         (pfx)
{
  std::map<std::string, std::string> labels{{"instrument", prms.instrument},
                                            {"partition", std::to_string(prms.partition)},
                                            {"detname", prms.alias},
                                            {"alias", prms.alias},
                                            {"eb", pfx}};
  exporter->constant("EB_EvPlDp", labels, eventPoolDepth());

  exporter->add("EB_EvAlCt", labels, MetricType::Counter, [&](){ return  eventAllocCnt();     });
  exporter->add("EB_EvFrCt", labels, MetricType::Counter, [&](){ return  eventFreeCnt();      });
  exporter->add("EB_EvOcCt", labels, MetricType::Gauge,   [&](){ return  eventOccCnt();       });
  //exporter->add("EB_EpOcCt", labels, MetricType::Gauge,   [&](){ return  epochOccCnt();       });
  exporter->add("EB_RxPdg",  labels, MetricType::Gauge,   [&](){ return _transport.pending(); });
  exporter->add("EB_BfInCt", labels, MetricType::Counter, [&](){ return _bufferCnt;           }); // Inbound
  exporter->add("EB_ToEvCt", labels, MetricType::Counter, [&](){ return  timeoutCnt();        });
  exporter->add("EB_FxUpCt", labels, MetricType::Counter, [&](){ return  fixupCnt();          });
  exporter->add("EB_CbMsMk", labels, MetricType::Gauge,   [&](){ return  missing();           });
  exporter->add("EB_EvAge",  labels, MetricType::Gauge,   [&](){ return  eventAge();          });
}

EbAppBase::~EbAppBase()
{
  for (auto& region : _region)
  {
    if (region)  free(region);
    region = nullptr;
  }
  _region.clear();
}

int EbAppBase::resetCounters()
{
  _bufferCnt = 0;
  if (_fixupSrc)  _fixupSrc->clear();
  if (_ctrbSrc)   _ctrbSrc ->clear();
  EventBuilder::resetCounters();

  return 0;
}

void EbAppBase::shutdown()
{
  _transport.shutdown();
}

void EbAppBase::disconnect()
{
  for (auto link : _links)  _transport.disconnect(link);
  _links.clear();

  _id           = -1;
  _contributors = 0;
  _contract     .fill(0);
  _bufRegSize   .clear();
  _maxBufSize   .clear();
  _maxTrSize    .clear();
}

void EbAppBase::unconfigure()
{
  if (!_links.empty())                  // Avoid dumping again if already done
    EventBuilder::dump(0);
  EventBuilder::clear();
}

int EbAppBase::startConnection(const std::string& ifAddr,
                               std::string&       port,
                               unsigned           nLinks)
{
  int rc = _transport.listen(ifAddr, port, nLinks);
  if (rc)
  {
    logging::error("%s:\n  Failed to initialize %s EbLfServer on %s:%s",
                   __PRETTY_FUNCTION__, "DRP", ifAddr.c_str(), port.c_str());
    return rc;
  }

  return 0;
}

int EbAppBase::connect(const EbParams& prms, size_t inpSizeGuess)
{
  unsigned nCtrbs = std::bitset<64>(prms.contributors).count();
  std::map<std::string, std::string> labels{{"instrument", prms.instrument},
                                            {"partition", std::to_string(prms.partition)},
                                            {"detname", prms.alias},
                                            {"alias", prms.alias},
                                            {"eb", _pfx}};

  _links        .resize(nCtrbs);
  _region       .resize(nCtrbs);
  _regSize      .resize(nCtrbs);
  _bufRegSize   .resize(nCtrbs);
  _maxTrSize    .resize(nCtrbs);
  _maxBufSize   .resize(nCtrbs);
  _id           = prms.id;
  _contributors = prms.contributors;
  _contract     = prms.contractors;
  _fixupSrc     = _exporter->histogram("EB_FxUpSc", labels, nCtrbs);
  _ctrbSrc      = _exporter->histogram("EB_CtrbSc", labels, nCtrbs); // Revisit: For testing

  int rc = linksConnect(_transport, _links, "DRP");
  if (rc)  return rc;

  // Set up a guess at the RDMA region now that we know the number of Contributors
  // If it's too small, it will be corrected during Configure
  for (unsigned i = 0; i < nCtrbs; ++i)
  {
    if (!_region[i])                    // No need to guess again
    {
      // Make a guess at the size of the Input region
      size_t regSizeGuess = (inpSizeGuess * _maxEvBuffers * _maxEntries +
                             roundUpSize(_maxTrBuffers * prms.maxTrSize[i]));
      //printf("*** EAB::connect: region %p, regSize %zu, regSizeGuess %zu\n",
      //       _region[i], _regSize[i], regSizeGuess);

      _region[i] = allocRegion(regSizeGuess);
      if (!_region[i])
      {
        logging::error("%s:\n  "
                       "No memory found for Input MR for %s[%d] of size %zd",
                       __PRETTY_FUNCTION__, "DRP", i, regSizeGuess);
        return ENOMEM;
      }

      // Save the allocated size, which may be more than the required size
      _regSize[i] = regSizeGuess;
    }

    //printf("*** EAB::connect: region %p, regSize %zu\n", _region[i], _regSize[i]);
    rc = _transport.setupMr(_region[i], _regSize[i]);
    if (rc)  return rc;
  }

  return 0;
}

int EbAppBase::configure(const EbParams& prms)
{
  int rc = _linksConfigure(prms, _links, _id, "DRP");
  if (rc)  return rc;

  // Code added here involving the links must be coordinated with the other side

  return 0;
}

int EbAppBase::_linksConfigure(const EbParams&            prms,
                               std::vector<EbLfSvrLink*>& links,
                               unsigned                   id,
                               const char*                peer)
{
  std::vector<EbLfSvrLink*> tmpLinks(links.size());

  for (auto link : links)
  {
    // Log a message so we can perhaps see the source of timeouts in UED,
    // where some servers and clients run on the same machine.  Compare
    // timestamps in /var/log/messages.
    logging::info("Starting to prepare link with a %s", peer);
    auto   t0(std::chrono::steady_clock::now());
    int    rc;
    size_t regSize;
    if ( (rc = link->prepare(id, &regSize, peer)) )
    {
      logging::error("%s:\n  Failed to prepare link with %s ID %d",
                     __PRETTY_FUNCTION__, peer, link->id());
      return rc;
    }
    unsigned rmtId     = link->id();
    tmpLinks[rmtId]    = link;

    _bufRegSize[rmtId] = regSize;
    _maxBufSize[rmtId] = regSize / (_maxEvBuffers * _maxEntries);
    _maxTrSize[rmtId]  = prms.maxTrSize[rmtId];
    regSize           += roundUpSize(_maxTrBuffers * _maxTrSize[rmtId]);  // Ctrbs don't have a transition space

    // Allocate the region, and reallocate if the required size is larger
    if (regSize > _regSize[rmtId])
    {
      if (_region[rmtId])  free(_region[rmtId]);

      _region[rmtId] = allocRegion(regSize);
      if (!_region[rmtId])
      {
        logging::error("%s:\n  "
                       "No memory found for Input MR for %s ID %d of size %zd",
                       __PRETTY_FUNCTION__, peer, rmtId, regSize);
        return ENOMEM;
      }

      // Save the allocated size, which may be more than the required size
      _regSize[rmtId] = regSize;
    }

    //printf("*** EAB::cfg: region %p, regSize %zu\n", _region[rmtId], regSize);
    if ( (rc = link->setupMr(_region[rmtId], regSize, peer)) )
    {
      logging::error("%s:\n  Failed to set up Input MR for %s ID %d, "
                     "%p:%p, size %zd", __PRETTY_FUNCTION__, peer, rmtId,
                     _region[rmtId], static_cast<char*>(_region[rmtId]) + regSize, regSize);
      return rc;
    }

    auto t1 = std::chrono::steady_clock::now();
    auto dT = std::chrono::duration_cast<ms_t>(t1 - t0).count();
    logging::info("Inbound link with %s ID %d configured in %lu ms",
                  peer, rmtId, dT);
  }

  links = tmpLinks;                     // Now in remote ID sorted order

  return 0;
}

int EbAppBase::process()
{
  int rc;

  // Pend for an input datagram and pass it to the event builder
  uint64_t  data;
  const int msTmo = 100;
  if ( (rc = _transport.pend(&data, msTmo)) < 0)
  {
    if (rc == -FI_ETIMEDOUT)
    {
      // This is called when contributions have ceased flowing
      EventBuilder::expired();          // Time out incomplete events
      rc = 0;
    }
    else if (_transport.pollEQ() == -FI_ENOTCONN)
      rc = -FI_ENOTCONN;
    else
      logging::error("%s:\n  pend() error %d (%s)\n",
                     __PRETTY_FUNCTION__, rc, strerror(-rc));
    return rc;
  }

  ++_bufferCnt;

  unsigned       flg = ImmData::flg(data);
  unsigned       src = ImmData::src(data);
  unsigned       idx = ImmData::idx(data);
  EbLfSvrLink*   lnk = _links[src];
  size_t         ofs = (ImmData::buf(flg) == ImmData::Buffer)
                     ? (                   idx * _maxBufSize[src]) // In batch/buffer region
                     : (_bufRegSize[src] + idx * _maxTrSize[src]); // Tr region for non-selected EB is after batch/buffer region
  const EbDgram* idg = static_cast<EbDgram*>(lnk->lclAdx(ofs));

  if (src != idg->xtc.src.value())
    logging::warning("Link src (%d) != dgram src (%d)", src, idg->xtc.src.value());

  _ctrbSrc->observe(double(src));       // Revisit: For testing

  if (unlikely(_verbose >= VL_BATCH))
  {
    unsigned    env = idg->env;
    uint64_t    pid = idg->pulseId();
    unsigned    ctl = idg->control();
    const char* svc = TransitionId::name(idg->service());
    printf("EbAp rcvd %9lu %15s[%8u]   @ "
           "%16p, ctl %02x, pid %014lx, env %08x,            src %2u, data %08lx, lnk %p, src %2u\n",
           _bufferCnt, svc, idx, idg, ctl, pid, env, lnk->id(), data, lnk, src);
  }
  else
  {
    auto svc = idg->service();
    if (svc != XtcData::TransitionId::L1Accept) {
      if (svc != XtcData::TransitionId::SlowUpdate) {
        logging::info("EbAppBase  saw %s @ %u.%09u (%014lx) from DRP ID %2u @ %16p (%08zx + %2u * %08zx)",
                      XtcData::TransitionId::name(svc),
                      idg->time.seconds(), idg->time.nanoseconds(),
                      idg->pulseId(), src, idg, _bufRegSize[src], idx, _maxTrSize[src]);
      }
      else {
        logging::debug("EbAppBase  saw %s @ %u.%09u (%014lx) from DRP ID %2u @ %16p (%08zx + %2u * %08zx)",
                       XtcData::TransitionId::name(svc),
                       idg->time.seconds(), idg->time.nanoseconds(),
                       idg->pulseId(), src, idg, _bufRegSize[src], idx, _maxTrSize[src]);
      }
    }
  }

  // Tr space bufSize value is irrelevant since idg has EOL set in that case
  EventBuilder::process(idg, _maxBufSize[src], data);

  return 0;
}

void EbAppBase::post(const EbDgram* const* begin, const EbDgram** const end)
{
  for (auto pdg = begin; pdg < end; ++pdg)
  {
    auto     idg = *pdg;
    unsigned src = idg->xtc.src.value();
    auto     lnk = _links[src];
    size_t   ofs = lnk->lclOfs(reinterpret_cast<const void*>(idg));
    unsigned idx = (ofs - _bufRegSize[src]) / _maxTrSize[src];
    uint64_t imm = ImmData::value(ImmData::Transition, _id, idx);

    if (unlikely(_verbose >= VL_EVENT))
      printf("EbAp posts transition buffer index %u to src %2u, %08lx\n",
             idx, src, imm);

    int rc = lnk->post(nullptr, 0, imm);
    if (rc)
    {
      logging::error("%s:\n  Failed to post transition buffer index %u to DRP ID %u: rc %d, imm %08lx",
                     __PRETTY_FUNCTION__, idx, src, rc, imm);
    }
  }
}

void EbAppBase::trim(unsigned dst)
{
  for (unsigned group = 0; group < _contract.size(); ++group)
  {
    _contract[group]  &= ~(1 << dst);
    //_receivers[group] &= ~(1 << dst);
  }
}

uint64_t EbAppBase::contract(const EbDgram* ctrb) const
{
  // This method is called when the event is created, which happens when the event
  // builder recognizes the first contribution.  This contribution contains
  // information from the L1 trigger that identifies which readout groups are
  // involved.  This routine can thus look up the expected list of contributors
  // (the contract) to the event for each of the readout groups and logically OR
  // them together to provide the overall contract.  The list of contributors
  // participating in each readout group is provided at configuration time.

  uint64_t contract = 0;
  uint16_t groups   = ctrb->readoutGroups();

  while (groups)
  {
    unsigned group = __builtin_ffs(groups) - 1;
    groups &= ~(1 << group);

    contract |= _contract[group];
  }
  return contract;
}

void EbAppBase::fixup(EbEvent* event, unsigned srcId)
{
  event->damage(Damage::DroppedContribution);

  if (!event->creator()->isEvent())
  {
    logging::warning("Fixup %s, %014lx, size %zu, source %d\n",
                     TransitionId::name(event->creator()->service()),
                     event->sequence(), event->size(), srcId);
  }

  _fixupSrc->observe(double(srcId));
}
