"""
collectors/process.py
Reads task_struct-equivalent fields from /proc/<pid>/status and /proc/<pid>/task/.

Every field here maps directly to a member of struct task_struct in
linux/sched.h, exported through the proc filesystem (fs/proc/array.c).
"""
import os


def _parse_status(path):
    data = {}
    try:
        with open(path) as f:
            for line in f:
                if ':' in line:
                    k, _, v = line.partition(':')
                    data[k.strip()] = v.strip()
    except Exception:
        pass
    return data


def _read(path, default=''):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def _get_thread(pid, tid):
    status = _parse_status(f'/proc/{pid}/task/{tid}/status')
    return {
        'tid':                       tid,
        'state':                     status.get('State', '?'),
        'name':                      status.get('Name', ''),
        'vm_rss_kb':                 status.get('VmRSS', '0 kB').replace(' kB', ''),
        'voluntary_ctxt_switches':   status.get('voluntary_ctxt_switches', '0'),
        'nonvoluntary_ctxt_switches':status.get('nonvoluntary_ctxt_switches', '0'),
        # Each thread has its own stack but shares the process mm_struct
        'shares_mm': True,
        'shares_files': True,
    }


def get_process_info(pid):
    """
    Return a structured dict of task_struct-equivalent fields for a process.

    Key mappings (kernel field → /proc export):
        task_struct.pid          → /proc/pid/status  Pid:
        task_struct.tgid         → /proc/pid/status  Tgid:   (== pid for main thread)
        task_struct.real_parent  → /proc/pid/status  PPid:
        task_struct.__state      → /proc/pid/status  State:
        task_struct.mm->total_vm → /proc/pid/status  VmSize:
        task_struct.mm->rss_stat → /proc/pid/status  VmRSS:
        task_struct.nvcsw        → /proc/pid/status  voluntary_ctxt_switches:
        task_struct.nivcsw       → /proc/pid/status  nonvoluntary_ctxt_switches:
    """
    status  = _parse_status(f'/proc/{pid}/status')
    cmdline = _read(f'/proc/{pid}/cmdline').replace('\x00', ' ')[:300]

    # Threads: each entry under /proc/<pid>/task/ is a thread (its own task_struct)
    threads = []
    try:
        tids = sorted(int(t) for t in os.listdir(f'/proc/{pid}/task') if t.isdigit())
        for tid in tids:
            threads.append(_get_thread(pid, tid))
    except Exception:
        pass

    # /proc/<pid>/stat — space-separated, field 39 is CPU affinity
    cpu_num = ''
    sched_policy = ''
    try:
        stat = _read(f'/proc/{pid}/stat').split()
        if len(stat) > 38:
            cpu_num = stat[38]
        if len(stat) > 40:
            sched_policy = stat[40]
    except Exception:
        pass

    # smaps_rollup — rolled-up memory breakdown (Linux 4.14+)
    smaps = {}
    try:
        with open(f'/proc/{pid}/smaps_rollup') as f:
            for line in f:
                if ':' in line:
                    k, _, v = line.partition(':')
                    smaps[k.strip()] = v.strip().replace(' kB', '')
    except Exception:
        pass

    # Open file descriptors — each is a struct file * in files_struct
    open_fds = []
    try:
        fd_dir = f'/proc/{pid}/fd'
        for fd in sorted(os.listdir(fd_dir)):
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
                fd_type = _classify_fd(target)
                open_fds.append({'fd': int(fd), 'target': target, 'type': fd_type})
            except Exception:
                pass
    except Exception:
        pass

    return {
        # ── Core task_struct fields ───────────────────────────────────────────
        'pid':                  pid,
        'name':                 status.get('Name', ''),
        'cmdline':              cmdline,
        'state':                status.get('State', ''),
        'tgid':                 status.get('Tgid', str(pid)),
        'ppid':                 status.get('PPid', ''),
        'uid':                  (status.get('Uid', '').split() or ['?'])[0],
        'num_threads':          status.get('Threads', '1'),
        'cpu_num':              cpu_num,

        # ── Memory (mm_struct) ────────────────────────────────────────────────
        'vm_size_kb':           status.get('VmSize',  '0 kB').replace(' kB', ''),
        'vm_rss_kb':            status.get('VmRSS',   '0 kB').replace(' kB', ''),
        'vm_peak_kb':           status.get('VmPeak',  '0 kB').replace(' kB', ''),
        'vm_stack_kb':          status.get('VmStk',   '0 kB').replace(' kB', ''),
        'vm_exe_kb':            status.get('VmExe',   '0 kB').replace(' kB', ''),
        'vm_lib_kb':            status.get('VmLib',   '0 kB').replace(' kB', ''),

        # ── Scheduler ────────────────────────────────────────────────────────
        'voluntary_ctxt_switches':    status.get('voluntary_ctxt_switches', '0'),
        'nonvoluntary_ctxt_switches': status.get('nonvoluntary_ctxt_switches', '0'),

        # ── Threads (each is its own task_struct sharing tgid) ───────────────
        'thread_list': threads,

        # ── File descriptors (files_struct shared across threads) ─────────────
        'open_fds': open_fds[:40],   # cap at 40 for display

        # ── smaps rollup ─────────────────────────────────────────────────────
        'smaps': smaps,

        # ── Explanatory notes for the dashboard ──────────────────────────────
        'notes': {
            'tgid_vs_pid': (
                f'TGID={status.get("Tgid", pid)} PID={pid}. '
                'All threads share the same TGID (= PID of main thread). '
                'task_struct.tgid groups them into one process.'
            ),
            'thread_sharing': (
                'Threads share: mm_struct (address space), files_struct (FDs), '
                'signal handlers. Each thread has its own: kernel stack, '
                'registers, task_struct.pid, scheduling state.'
            ),
            'cpu_rings': (
                'This process runs in ring 3 (user mode). '
                'Every system call (open, read, mmap…) triggers a SYSCALL '
                'instruction that switches the CPU to ring 0 (kernel mode), '
                'executes kernel code, then returns via SYSRET.'
            ),
            'vm_rss_vs_vm_size': (
                f'VmSize={status.get("VmSize","?")} is the total virtual '
                f'address space (committed pages). '
                f'VmRSS={status.get("VmRSS","?")} is only the physically '
                'present pages. Difference = demand-paged (not yet faulted in).'
            ),
        }
    }


def _classify_fd(target):
    if target.startswith('socket:'):
        return 'socket'
    if target.startswith('pipe:'):
        return 'pipe'
    if target.startswith('/dev/'):
        return 'device'
    if target.startswith('['):
        return 'special'
    return 'file'
