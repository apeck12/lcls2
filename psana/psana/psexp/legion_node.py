from psana.psexp import mode, StepHistory, repack_for_bd, repack_with_step_dg, repack_with_mstep_dg, PacketFooter
from psana.psexp import EventBuilderManager, TransitionId, Events
from psana.psexp.run import RunLegion
from psana import dgram
import numpy as np
import time
import logging
logger = logging.getLogger(__name__)

evt_kinds = {
    0: "ClearReadout",
    1: "Reset",
    2: "Configure",
    3: "Unconfigure",
    4: "BeginRun",
    5: "EndRun",
    6: "BeginStep",
    7: "EndStep",
    8: "Enable",
    9: "Disable",
    10: "SlowUpdate",
    11: "Unused_11",
    12: "L1Accept",
    13: "NumberOf",
}

pygion = None
if mode == 'legion':
    import pygion
    import sys
    from pygion import task, RW, RO, WD, Partition, Ipartition, Region, Ispace, Domain, Reduce
else:
    # Nop when not using Legion
    def task(fn=None, **kwargs):
        if fn is None:
            return lambda fn: fn
        return fn
    RO=True
    WD=True
    def Reduce(r):
        pass

run_objs = []

class LEventBuilderNode(object):
    def __init__(self, bd_size, point_ofst, configs, dsparms, dm):
        self.configs    = configs
        self.dsparms    = dsparms
        self.dm         = dm
        self.step_hist  = StepHistory(bd_size+1, len(self.configs))
        # Collecting Smd0 performance using prometheus
        self.c_sent     = dsparms.prom_man.get_metric('psana_eb_sent')
        self.requests   = []
        self.bd_size = bd_size
        self.point_ofst = point_ofst
        for i in range(self.bd_size):
            self.requests.append(0)

    ''' Sends a processed batch to Big Data Task '''
    def _send_to_dest(self, dest_rank, smd_batch_dict,
                      step_batch_dict, eb_man, batches):

        smd_batch, _ = smd_batch_dict[dest_rank]

        missing_step_views = self.step_hist.get_buffer(dest_rank)

        batches[dest_rank] = repack_for_bd(smd_batch,
                                           missing_step_views,
                                           self.configs,
                                           client=dest_rank)

        self.requests[dest_rank-1] = run_bigdata_task_psana2(
            batches[dest_rank], point=dest_rank)

        step_batch, _ = step_batch_dict[dest_rank]

        if eb_man.eb.nsteps > 0 and memoryview(step_batch).nbytes > 0:
            step_pf = PacketFooter(view=step_batch)
            self.step_hist.extend_buffers(
                step_pf.split_packets(), dest_rank, as_event=True)

@task(inner=True)
def eb_task(smd_chunk, idx):
    run  = run_objs[idx]
    eb = run.ds.eb
    eb_man = EventBuilderManager(smd_chunk, run.configs, run.dsparms, run)
    batches = {}
    i=0
    for smd_batch_dict, step_batch_dict  in eb_man.batches():
        # send to any bigdata nodes.
        smd_batch, _ = smd_batch_dict[0]
        step_batch, _ = step_batch_dict[0]
        point = i%eb.bd_size + 1 # smd0 is point 0
        logger.debug(f'bd_size {eb.bd_size}, point {point}')
        i=i+1

        #start on the packaging for BD nodes
        missing_step_views = eb.step_hist.get_buffer(point)
        batches[point] = repack_for_bd(smd_batch,
                                       missing_step_views,
                                       eb.configs,
                                       client=point)

        if eb.requests[point-1] != 0:
            eb.requests[point-1].get()

        eb.requests[point-1] = run_bigdata_task_psana2(
            batches[point], idx, point=point+eb.point_ofst)

        logger.debug(f'eb_task launched big data task point {point} {time.monotonic()}')

        # sending data to prometheus
        logger.debug(f'node: eb sent {eb_man.eb.nevents} events ({memoryview(smd_batch).nbytes} bytes) to task bd{point}')

        eb.c_sent.labels('evts', point).inc(eb_man.eb.nevents)
        eb.c_sent.labels('batches', point).inc()
        eb.c_sent.labels('MB', point).inc(
            memoryview(batches[point]).nbytes/1e6)

        if eb_man.eb.nsteps > 0 and memoryview(step_batch).nbytes > 0:
            step_pf = PacketFooter(view=step_batch)
            eb.step_hist.extend_buffers(step_pf.split_packets(),
                                        point, as_event=True)
        batches = {}
        # Check if any of the bds need missing steps from the last batch
        for i in range(eb.bd_size):
            logger.debug(f'i={i} n_bd_nodes={eb.bd_size}')
            client_id = i+1
            missing_step_views = eb.step_hist.get_buffer(client_id)
            batches[client_id] = repack_for_bd(bytearray(),
                                       missing_step_views,
                                       eb.configs, client=client_id)

            if batches[client_id]:
                if eb.requests[client_id-1] != 0:
                    eb.requests[client_id-1].get()

                eb.requests[client_id-1] = run_bigdata_task_psana2(batches[client_id],
                                                                 idx, point=client_id+eb.point_ofst)
                logger.debug(f'eb task sent missing step to big data task {client_id} {time.monotonic()}')
    return i



# builds batches and launches eb_tasks
# Use futures to track task completion for next set of batches
# Batches are patched to reflect missing SU datagrams
class LSmd0(object):
    """ Sends blocks of smds to eb nodes
    Identifies limit timestamp of the slowest detector then
    sends all smds within that timestamp.
    """
    def __init__(self, eb_size, configs, smdr_man, dsparms):
        self.smdr_man = smdr_man
        self.configs = configs
        self.smd_size =  eb_size
        assert self.smd_size > 0
        self.step_hist = StepHistory(self.smd_size+1, len(self.configs))
        # Collecting Smd0 performance using prometheus
        self.c_sent = dsparms.prom_man.get_metric('psana_smd0_sent')

    """ support separate chunks for step and smds
    """
    def get_region_step_smd_chunk(self):
        # pack_smds and step separately
        pack_smds = {}
        pack_steps = {}

        # internally smdreader i.e. smdr_man.smdr
        # keeps track of start,step,buffer position
        # for this chunk
        for i_chunk in self.smdr_man.chunks():
            # Check missing steps: assume only single eb
            # Initially returns empty views
            # Next update (via extend_buffers_state) will record new transition
            # history
            # note: to test with task run_smd0_with_region_task_psana2
            # invoke get_buffer_only
            missing_step_views = self.step_hist.get_buffer(1)
            # get step entries and add them to the step region
            step_views = [self.smdr_man.smdr.show(i, step_buf=True)
                          for i in range(self.smdr_man.n_files)]
            # append the new step view to the end of the buffer
            extend_buffers = self.step_hist.extend_buffers_state(step_views,1)
            # pack only the buffer without steps
            # pack step views only if there are missing steps
            # add those to legion's step region
            eb_id = 1
            step_only = 1
            if extend_buffers:
                pack_steps[1] = self.smdr_man.smdr.repack_parallel(missing_step_views,
                                                                   eb_id, step_only)
            else:
                pack_steps[1] = bytearray()
            pack_smds[1] = self.smdr_man.smdr.repack_only_buf(eb_id)
            yield pack_smds[1], pack_steps[1]


    def start(self):
        rankreq = np.empty(self.smd_size, dtype='i')
        requests = []
        future_eb = [None]*(self.smd_size+1)
        logger.debug(f'Legion SMD0 smd_size = {self.smd_size}')

        # SmdReaderManager has starting index and block size
        # that it needs to share later when data are packaged
        # for sending to EventBuilders.
        repack_smds = {}

        for i_chunk in self.smdr_man.chunks():
            st_req = time.monotonic()
            logger.debug(f' smd0 task got i_chunk={i_chunk} {st_req}')
            # task to send this chunk with history
            point = i_chunk%self.smd_size + 1

            if future_eb[point] != None:
                future_eb[point].get()

            # Check missing steps for the current client
            missing_step_views = self.step_hist.get_buffer(point,
                                                           smd0=True)
            # returns a view into each file for step entries
            step_views = [self.smdr_man.smdr.show(i, step_buf=True)
                          for i in range(self.smdr_man.n_files)]

            # Update step buffers (after getting the missing steps)
            self.step_hist.extend_buffers(step_views, point)
            # combine steps views + actual data
            repack_smds[point] = self.smdr_man.smdr.repack_parallel(
                missing_step_views, point)

            future_eb[point] = run_smd_task_psana2(
                bytearray(repack_smds[point]), point-1)

            logger.debug(f'smd0 done launching  eb task {point} {time.monotonic()}')
            en_req = time.monotonic()
            # sending data to prometheus
            self.c_sent.labels('evts', point).inc(
                self.smdr_man.got_events)

            self.c_sent.labels('batches', point).inc()
            self.c_sent.labels('MB', point).inc(
                memoryview(repack_smds[point]).nbytes/1e6)

            self.c_sent.labels('seconds', point).inc(en_req - st_req)
            found_endrun = self.smdr_man.smdr.found_endrun()
            if found_endrun:
                break

        # end for (smd_chunk, step_chunk)
        for i in range(self.smd_size):
            point=i+1
            # build the missing step views and then check the futures
            missing_step_views = self.step_hist.get_buffer(point,
                                                           smd0=True)

            repack_smds[point] = self.smdr_man.smdr.repack_parallel(
                missing_step_views, point, only_steps=1)

            if memoryview(repack_smds[point]).nbytes > 0:
                future_eb[point] = run_smd_task_psana2(
                    bytearray(repack_smds[point]), point-1)
            else:
                future_eb[point] = 0

@task(inner=True)
def run_bigdata_task_psana2(batch, idx):
    run = run_objs[idx]
    for evt in batch_events(batch, run):
        run.event_fn(evt, run.det)

def run_smd_task_psana2(smd_chunk,idx):
    f =  eb_task(smd_chunk, idx, point=idx-1)
    return f

# use regions for transition data
# 1 partition  = [start:end]
def make_ipartition(r_ispace, start, end):
    colors = [1]
    index_spaces = []
    IP1 = Ipartition.pending(r_ispace, [1])
    index_spaces.append(Ispace([end-start],[start]))
    IP1.union([0], index_spaces)
    return IP1

def eb_debug_batches(idx, smd_batch, cnt):
    run = run_objs[idx]
    pf = PacketFooter(view=smd_batch, num_views=cnt)
    for j, chunks in enumerate(pf.split_multiple_packets()):
        offsets = [0] * pf.n_packets
        logger.debug(f'n_packets={pf.n_packets}, partition[{j}]')
        for i, chunk in enumerate(chunks):
            logger.debug(f'----File %d----' % (i))
            while offsets[i] < pf.get_size(i):
                # Creates a dgram from this chunk at the given offset.
                d = dgram.Dgram(view=chunk, config=run.configs[i], offset=offsets[i])
                logger.debug(f'timestamp: %s : size: %d %s' % (str(d.timestamp()), d._size, evt_kinds[d.service()]))
                offsets[i] += d._size

# debug task that logs all the datagrams
@task(privileges=[RO])
def eb_task_debug_multiple(R, idx, smd_batch, cnt):
    logger.debug(f'EB_Task_With_Multiple_Region_DEBUG: Subregion has volume %s extent %s bounds %s' % (
        R.ispace.volume, R.ispace.domain.extent, R.ispace.bounds))

    if smd_batch:
        logger.debug(f'--------------L1Accept Dgrams---------------')
        eb_debug_batches(idx, smd_batch, 1)
    if cnt:
        logger.debug(f'-------------Transition Dgrams--------------')
        eb_debug_batches(idx, bytearray(R.x), cnt)


# debug task that logs all the datagrams
@task(privileges=[RO])
def eb_task_debug(R, smd_batch, idx):
    logger.debug(f'EB_Task_With_Region_DEBUG: Subregion has volume %s extent %s bounds %s' % (
        R.ispace.volume, R.ispace.domain.extent, R.ispace.bounds))
    run = run_objs[idx]
    pf = PacketFooter(view=smd_batch)
    chunks = pf.split_packets()
    logger.debug(f'------------------EB Task Dgrams-------------------')
    offsets = [0] * pf.n_packets
    for i, chunk in enumerate(chunks):
        logger.debug(f'----File %d----' % (i))
        while offsets[i] < pf.get_size(i):
            # Creates a dgram from this chunk at the given offset.
            d = dgram.Dgram(view=chunk, config=run.configs[i], offset=offsets[i])
            logger.debug(f'timestamp: %s : size: %d %s' % (str(d.timestamp()), d._size, evt_kinds[d.service()]))
            offsets[i] += d._size

    pf = PacketFooter(view=bytearray(R.x))
    chunks = pf.split_packets()
    logger.debug(f'-----------EB Task Transition Region Dgrams-------------')
    offsets = [0] * pf.n_packets
    for i, chunk in enumerate(chunks):
        logger.debug(f'----File %d----' % (i))
        while offsets[i] < pf.get_size(i):
            # Creates a dgram from this chunk at the given offset.
            d = dgram.Dgram(view=chunk, config=run.configs[i], offset=offsets[i])
            logger.debug(f'timestamp: %s : size: %d %s' % (str(d.timestamp()), d._size, evt_kinds[d.service()]))
            offsets[i] += d._size

# EB task with a region for transition datagrams
@task(privileges=[RO])
def eb_task_with_region(R, smd_batch, idx):
    ''' log the datagrams
    '''
    eb_task_debug(R, smd_batch, idx)
    run = run_objs[idx]
    eb = run.ds.eb
    eb_man = EventBuilderManager(smd_batch, run.configs, run.dsparms, run)
    batches = {}
    for smd_batch_dict in eb_man.smd_batches():
        # send to any bigdata nodes if destination is required
        smd_batch, _ = smd_batch_dict[0]
        batches[1] = repack_with_step_dg(smd_batch,
                                         bytearray(R.x),
                                         eb.configs)
        run = run_objs[idx]
        for evt in batch_events(batches[1], run):
            run.event_fn(evt, run.det)

def smd_batches_with_transitions(smd_batch, run, R, num_dgrams):
    eb = run.ds.eb
    eb_man = EventBuilderManager(smd_batch, run.configs, run.dsparms, run)
    batches = {}
    for smd_batch_dict in eb_man.smd_batches():
        smd_batch, _ = smd_batch_dict[0]
        batches[0] = repack_with_mstep_dg(smd_batch,
                                         bytearray(R.x),
                                         eb.configs, num_dgrams)
        yield batches[0]

def smd_batches_without_transitions(smd_batch, run):
    eb = run.ds.eb
    eb_man = EventBuilderManager(smd_batch, run.configs, run.dsparms, run)
    batches = {}
    for smd_batch_dict in eb_man.smd_batches():
        smd_batch, _ = smd_batch_dict[0]
        yield smd_batch

# EB task with a region for transition datagrams
@task(privileges=[RO])
def eb_task_with_multiple_region(R, smd_batch, idx, num_dgrams):
    ''' log the datagrams
    eb_task_debug_multiple(R, idx, smd_batch, num_dgrams)
    '''
    logger.debug(f'EB_Task_With_Multiple_Region: Subregion has volume %s extent %s bounds %s' % (
        R.ispace.volume, R.ispace.domain.extent, R.ispace.bounds))
    run = run_objs[idx]
    for batch in smd_batches_with_transitions(smd_batch, run, R, num_dgrams):
        for evt in batch_events(batch, run):
            run.event_fn(evt, run.det)

def eb_reduc(R, Redc, smd_batch, idx, num_dgrams):
    ''' log the datagrams
    eb_task_debug_multiple(R, idx, smd_batch, num_dgrams)
    '''
    logger.debug(f'EB_Task_With_Multiple_Region_Reduc: Subregion has volume %s extent %s bounds %s' % (
        R.ispace.volume, R.ispace.domain.extent, R.ispace.bounds))
    logger.debug(f'EB_Task_With_Multiple_Region_Reduc: Subregion Reduc has volume %s extent %s bounds %s' % (
        Redc.ispace.volume, Redc.ispace.domain.extent, Redc.ispace.bounds))
    run = run_objs[idx]
    for batch in smd_batches_with_transitions(smd_batch, run, R, num_dgrams):
        for evt in batch_events(batch, run):
            run.reduc_fn(Redc.rval, evt, run.det)

# EB task with a region for transition datagrams
# and a reduction region
@task(privileges=[RO,Reduce('+')])
def eb_reduc_task_sum(R, Redc, smd_batch, idx, num_dgrams):
    eb_reduc(R, Redc, smd_batch, idx, num_dgrams)

@task(privileges=[RO,Reduce('-')])
def eb_reduc_task_minus(R, Redc, smd_batch, idx, num_dgrams):
    eb_reduc(R, Redc, smd_batch, idx, num_dgrams)

@task(privileges=[RO,Reduce('min')])
def eb_reduc_task_min(R, Redc, smd_batch, idx, num_dgrams):
    eb_reduc(R, Redc, smd_batch, idx, num_dgrams)

@task(privileges=[RO,Reduce('max')])
def eb_reduc_task_max(R, Redc, smd_batch, idx, num_dgrams):
    eb_reduc(R, Redc, smd_batch, idx, num_dgrams)

@task(privileges=[RO,Reduce('/')])
def eb_reduc_task_div(R, Redc, smd_batch, idx, num_dgrams):
    eb_reduc(R, Redc, smd_batch, idx, num_dgrams)

@task(privileges=[RO,Reduce('*')])
def eb_reduc_task_mult(R, Redc, smd_batch, idx, num_dgrams):
    eb_reduc(R, Redc, smd_batch, idx, num_dgrams)


@task(privileges=[WD])
def fill_task(R):
    pygion.fill(R, 'x', 0)

@task
def make_region_task(size):
    R = Region([size], {'x': pygion.int8})
    return R

@task(privileges=[WD])
def fill_data(R, data):
    logger.debug(f'Fill_Data_Task: Subregion has volume %s extent %s bounds %s' % (
        R.ispace.volume, R.ispace.domain.extent, R.ispace.bounds))
    np.copyto(R.x,bytearray(data))


# If step_data exists -> Partition -> [0,len(step_data)-1]
# Launch fill_data task to copy the step_data
def check_partition(R, P, step_data):
    # make a new partition only if transitions have occured in the chunk
    if len(step_data) != 0:
        start = 0
        end = len(bytearray(step_data))
        # previous old partition instance can be cleared
        fill_task(P[0])
        IP = make_ipartition(R.ispace, start, end)
        P = Partition(R, IP)
        fill_data(P[0], bytearray(step_data))
    return P

# Partition -> [P.ispace.volume, P.ispace.volume + len(step_data)-1]
# Fill the new partition with step_data
def fill_new_subregion(R, P, step_data):
    index_space = []
    start = P.ispace.volume
    size = len(bytearray(step_data))
    IP = Ipartition.pending(R.ispace, [1])
    index_space.append(Ispace([size], [start]))
    IP.union([0], index_space)
    P = Partition(R, IP)
    logger.debug(f'Fill_new_subregion: Subregion has bounds %s' % (index_space[0].bounds))
    fill_data(P[0], bytearray(step_data))
    return P

# Partition -> [0,size_old-1] U [size_old, size_new-1]
def union_partitions(R, Pold, Pnew):
    index_space = []
    size_old = Pold.ispace.volume
    size_new = Pnew.ispace.volume
    IP = Ipartition.pending(R.ispace, [1])
    index_space.append(Ispace([size_old], [0]))
    index_space.append(Ispace([size_new], [size_old]))
    logger.debug(f'union_partitions: Subregion[0] has bounds %s' % (index_space[0].bounds))
    logger.debug(f'union_partitions: Subregion[1] has bounds %s' % (index_space[1].bounds))
    IP.union([0], index_space)
    P = Partition(R, IP)
    return P

# 1) check if new transition/step data exists
# 2) if True:
#      a) create new partition and fill subregion ->fill_new_subregion
#      b) merge old partition and new partition and return new merged partition -> union_partitions
def update_partition(R, P, step_data,cnt):
    if len(step_data) != 0:
        Pnew = fill_new_subregion(R, P[0], step_data)
        Punion = union_partitions(R, P[0], Pnew[0])
        cnt=cnt+1
        return Punion, cnt
    return P,cnt

def smd_chunks_steps(run):
    return run.ds.smd0.get_region_step_smd_chunk()

# This is the entry task for SMD0 with a Region for Transition Datagrams
@task(inner=True, replicable=True)
def run_smd0_with_region_task_psana2(idx):
    R = make_region_task(sys.maxsize).get() # specify mapper option -dm:exact_region
    IP = make_ipartition(R.ispace, 0, 0)
    P = Partition(R, IP)
    fill_task(P[0])
    run = run_objs[idx]
    for smd_data, step_data in smd_chunks_steps(run):
        # make a new partition only if additional transitions have occured in the chunk
        P = check_partition(R, P, step_data)
        eb_task_with_region(P[0], bytearray(smd_data), idx)
    pygion.execution_fence(block=True)

def perform_eb(R,P,smd_data,step_data,global_procs,pt,num_partitions,idx):
    if global_procs==1:
        pt=-1
    else:
        pt=pt+1
        pt=pt%(global_procs-1)
    # make a new partition only if additional transitions have occured in the chunk
    P, num_partitions = update_partition(R, P, step_data,num_partitions)
    eb_task_with_multiple_region(P[0], bytearray(smd_data), idx, num_partitions, point=pt+1)
    return P, pt, num_partitions


def perform_eb_reduc(R,P,smd_data,step_data,global_procs,pt,num_partitions,idx,reduc_region,reduc_type):
    if global_procs==1:
        pt=-1
    else:
        pt=pt+1
        pt=pt%(global_procs-1)

    eb_reduc_task = {
        '+': eb_reduc_task_sum,
        '-': eb_reduc_task_minus,
        'min': eb_reduc_task_min,
        'max': eb_reduc_task_max,
        '/': eb_reduc_task_div,
        '*': eb_reduc_task_mult
    }
    reduc_task = eb_reduc_task.get(reduc_type, None)
    assert reduc_task !=  None
    # make a new partition only if additional transitions have occured in the chunk
    P, num_partitions = update_partition(R, P, step_data,num_partitions)
    reduc_task(P[0], reduc_region,
               bytearray(smd_data), idx, num_partitions,
               point=pt+1)
    return P, pt, num_partitions

def init_region_partition():
    R = make_region_task(sys.maxsize).get()
    IP = make_ipartition(R.ispace, 0, -1)
    P = Partition(R, IP)
    fill_task(P[0])
    return R, P

# This is the entry task for SMD0 with a Region for Transition Datagrams with multiple Partitions
@task(inner=True, replicable=True)
def run_smd0_with_region_task_multiple_psana2(idx):
    global_procs = pygion.Tunable.select(pygion.Tunable.GLOBAL_PYS).get()
    num_partitions=0
    point=-1
    R, P = init_region_partition()
    run = run_objs[idx]
    for smd_data, step_data in smd_chunks_steps(run):
        P, point, num_partitions = perform_eb(R,P,smd_data,step_data,global_procs,point,num_partitions,idx)
    pygion.execution_fence(block=True)


@task(privileges=[RO])
def run_smd0_reduc_final_task(R, idx):
    run = run_objs[idx]
    run.reduc_final_fn(R.rval)

# perform the reduction operation
def perform_reduc_op(Redc, idx):
    global_procs = pygion.Tunable.select(pygion.Tunable.GLOBAL_PYS).get()
    num_partitions=0
    point=-1
    R, P = init_region_partition()
    run = run_objs[idx]
    reduc_type = run.reduc_privileges

    for smd_data, step_data in smd_chunks_steps(run):
        P, point, num_partitions = perform_eb_reduc(R,P,smd_data,step_data,
                                                    global_procs,point,num_partitions,
                                                    idx,Redc,reduc_type)
    pygion.execution_fence(block=True)
    # callback for final reduction
    if run.reduc_final_fn:
        run_smd0_reduc_final_task(Redc,idx,point=0)

# This is the entry task for SMD0 with
# a) a Region for Transition Datagrams with multiple Partitions
# b) Reduction Operation with a callback
@task(inner=True, replicable=True)
def run_smd0_reduc_task(idx):
    run = run_objs[idx]
    field_dict = {"rval":getattr(pygion, run.reduc_rtype)}
    reduc_region = Region(run.reduc_shape, field_dict)
    pygion.fill(reduc_region, 'rval', run.reduc_fill_val)
    perform_reduc_op(reduc_region, idx)

@task(privileges=[RO])
def dump_reduc(R):
    print('Dumping Reduc Values')
    print(R.rval)

@task(inner=True)
def run_smd0_task_psana2(idx):
    run = run_objs[idx]
    run.ds.smd0.start()
    # Block before returning so that the caller can
    # use this task's future for synchronization
    pygion.execution_fence(block=True)

def smd_chunks(run):
    for smd_chunk, update_chunk in run.smdr_man.chunks():
        yield smd_chunk

@task(inner=True)
def run_smd0_task(run):
    global_procs = pygion.Tunable.select(pygion.Tunable.GLOBAL_PYS).get()
    for i, smd_chunk in enumerate(smd_chunks(run)):
        run_smd_task(smd_chunk, run, point=i)
    # Block before returning so that the caller can use this task's future for synchronization
    pygion.execution_fence(block=True)

def smd_batches(smd_chunk, run):
    eb_man = EventBuilderManager(smd_chunk, run.configs, run.dsparms, run) 
    for smd_batch_dict, step_batch_dict in eb_man.batches():
        smd_batch, _ = smd_batch_dict[0]
        yield smd_batch

@task(inner=True)
def run_smd_task(smd_chunk, run):
    for i, smd_batch in enumerate(smd_batches(smd_chunk, run)):
        run_bigdata_task(smd_batch, run, point=i)

def batch_events(smd_batch, run):
    batch_iter = iter([smd_batch, bytearray()])
    def get_smd():
        for this_batch in batch_iter:
            return this_batch
    events = Events(run.configs, run.dm, run.dsparms, 
            filter_callback=run.dsparms.filter, get_smd=get_smd)
    for i, evt in enumerate(events):
        logger.debug(f'evt[{i}] = {evt_kinds[evt.service()]}')
        if evt.service() != TransitionId.L1Accept: continue
        yield evt

@task
def run_bigdata_task(batch, run):
    for evt in batch_events(batch, run):
        run.event_fn(evt, run.det)

run_to_process = []
def analyze(run, event_fn=None, det=None):
    run.event_fn = event_fn
    run.det = det
    if pygion.is_script:
        num_procs = pygion.Tunable.select(pygion.Tunable.GLOBAL_PYS).get()
        bar = pygion.c.legion_phase_barrier_create(pygion._my.ctx.runtime, pygion._my.ctx.context, num_procs)
        pygion.c.legion_phase_barrier_arrive(pygion._my.ctx.runtime, pygion._my.ctx.context, bar, 1)
        global_task_registration_barrier = pygion.c.legion_phase_barrier_advance(pygion._my.ctx.runtime, pygion._my.ctx.context, bar)
        pygion.c.legion_phase_barrier_wait(pygion._my.ctx.runtime, pygion._my.ctx.context, bar)
        run_objs.append(run)
        if run.reduc:
            return run_smd0_reduc_task(len(run_objs)-1,point=0)
        else:
            return run_smd0_with_region_task_multiple_psana2(len(run_objs)-1,point=0)
            #return run_smd0_with_region_task_psana2(len(run_objs)-1)
    else:
        run_objs.append(run)
    

if pygion is not None and not pygion.is_script:
    @task(top_level=True)
    def legion_main():
        for i, _ in enumerate(run_objs):
            if run.reduc:
                run_smd0_reduc_task(i,point=0)
            else:
                run_smd0_with_region_task_multiple_psana2(i,point=0)
                #run_smd0_with_region_task_psana2(i)
