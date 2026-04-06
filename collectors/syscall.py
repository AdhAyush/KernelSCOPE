"""
collectors/syscall.py
Attaches strace to a running process for N seconds, captures the
frequency summary, and categorises each syscall by kernel subsystem.

Each syscall represents a ring-3 → ring-0 transition via the SYSCALL
instruction on x86-64 (or svc on ARM). The CPU saves registers to the
kernel stack, looks up the syscall number in sys_call_table[], and
dispatches to the handler.
"""
import subprocess
import time

# ── Categorisation ────────────────────────────────────────────────────────────

CATEGORIES = {
    'file_io': [
        'open', 'openat', 'openat2', 'read', 'write', 'close',
        'stat', 'lstat', 'fstat', 'newfstatat', 'statx',
        'access', 'faccessat', 'lseek', 'pread64', 'pwrite64',
        'readv', 'writev', 'preadv', 'pwritev',
        'fsync', 'fdatasync', 'truncate', 'ftruncate',
        'rename', 'renameat', 'renameat2',
        'unlink', 'unlinkat', 'mkdir', 'mkdirat', 'rmdir',
        'getdents', 'getdents64', 'readlink', 'readlinkat',
        'chmod', 'fchmod', 'chown', 'fchown', 'lchown',
        'dup', 'dup2', 'dup3', 'fcntl', 'ioctl',
        'sendfile', 'sendfile64', 'splice', 'tee',
    ],
    'memory': [
        'mmap', 'mmap2', 'munmap', 'brk', 'mprotect',
        'mremap', 'madvise', 'mlock', 'munlock', 'mlock2',
        'msync', 'mincore', 'mbind', 'set_mempolicy',
        'get_mempolicy', 'remap_file_pages',
        'memfd_create', 'shm_open', 'shmget', 'shmat', 'shmdt', 'shmctl',
    ],
    'process': [
        'clone', 'clone3', 'fork', 'vfork', 'execve', 'execveat',
        'wait4', 'waitpid', 'waitid',
        'exit', 'exit_group',
        'getpid', 'getppid', 'gettid', 'getuid', 'getgid',
        'sched_yield', 'sched_getparam', 'sched_setparam',
        'sched_getscheduler', 'sched_setscheduler',
        'sched_getaffinity', 'sched_setaffinity',
        'nanosleep', 'clock_nanosleep', 'pause',
        'setpriority', 'getpriority', 'nice',
        'kill', 'tkill', 'tgkill',
        'rt_sigaction', 'rt_sigprocmask', 'rt_sigreturn',
        'sigaltstack', 'ptrace', 'prctl',
    ],
    'network': [
        'socket', 'bind', 'connect', 'accept', 'accept4', 'listen',
        'send', 'recv', 'sendto', 'recvfrom',
        'sendmsg', 'recvmsg', 'sendmmsg', 'recvmmsg',
        'setsockopt', 'getsockopt',
        'getsockname', 'getpeername', 'socketpair', 'shutdown',
        'poll', 'ppoll', 'select', 'pselect6',
        'epoll_create', 'epoll_create1', 'epoll_ctl', 'epoll_wait', 'epoll_pwait',
        'io_uring_setup', 'io_uring_enter', 'io_uring_register',
    ],
    'ipc': [
        'pipe', 'pipe2',
        'mq_open', 'mq_send', 'mq_timedsend',
        'mq_receive', 'mq_timedreceive', 'mq_unlink', 'mq_getsetattr',
        'semget', 'semop', 'semtimedop', 'semctl',
        'msgget', 'msgsnd', 'msgrcv', 'msgctl',
        'inotify_init', 'inotify_init1', 'inotify_add_watch', 'inotify_rm_watch',
        'eventfd', 'eventfd2', 'signalfd', 'signalfd4', 'timerfd_create',
    ],
}

CATEGORY_COLORS = {
    'file_io':  '#185FA5',
    'memory':   '#BA7517',
    'process':  '#993C1D',
    'network':  '#0F6E56',
    'ipc':      '#534AB7',
    'other':    '#5F5E5A',
}


def _categorise(name):
    for cat, names in CATEGORIES.items():
        if name in names:
            return cat
    return 'other'


def _parse_strace_summary(text):
    """
    strace -c writes a summary table to stderr like:
    % time     seconds  usecs/call     calls    errors syscall
    ------ ----------- ----------- --------- --------- ----------------
      42.3    0.001234          12       100         5 read
    """
    results = []
    in_table = False

    for line in text.strip().split('\n'):
        line = line.strip()
        if '% time' in line and 'syscall' in line:
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith('---') or not line:
            continue
        if line.startswith('100.') or line.startswith('--'):
            continue

        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            pct        = float(parts[0])
            count_idx  = 3
            name_idx   = 5 if len(parts) >= 6 else 4
            errors_idx = 4 if len(parts) >= 6 else None

            count  = int(parts[count_idx])
            errors = int(parts[errors_idx]) if errors_idx and parts[errors_idx].isdigit() else 0
            name   = parts[name_idx] if name_idx < len(parts) else parts[-1]
            usecs  = int(parts[2]) if parts[2].isdigit() else 0

            cat = _categorise(name)
            results.append({
                'name':          name,
                'count':         count,
                'errors':        errors,
                'pct_time':      round(pct, 2),
                'usecs_per_call': usecs,
                'category':      cat,
                'color':         CATEGORY_COLORS.get(cat, CATEGORY_COLORS['other']),
                'ring_note':     f'{name}() → sys_{name}() in ring 0',
            })
        except (ValueError, IndexError):
            continue

    results.sort(key=lambda x: x['count'], reverse=True)
    return results[:20]


def trace_syscalls(pid, duration=3):
    try:
        proc = subprocess.Popen(
            ['strace', '-c', '-p', str(pid), '-e', 'trace=all'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(duration)
        proc.terminate()
        try:
            _, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()

        results = _parse_strace_summary(stderr)

        category_totals = {}
        for r in results:
            cat = r['category']
            category_totals[cat] = category_totals.get(cat, 0) + r['count']

        return {
            'pid':              pid,
            'duration_s':       duration,
            'syscalls':         results,
            'category_totals':  category_totals,
            'category_colors':  CATEGORY_COLORS,
            'note': (
                'Each syscall crosses the ring-3 → ring-0 boundary. '
                'The SYSCALL instruction saves RIP/RSP to the kernel stack, '
                'loads the kernel GS segment, and jumps to system_call_dispatch().'
            ),
        }
    except FileNotFoundError:
        return {
            'error':    'strace not found. Install: sudo apt install strace',
            'syscalls': [],
        }
    except PermissionError:
        return {
            'error': (
                'Permission denied for ptrace. '
                'Either run as root, or: '
                'echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope'
            ),
            'syscalls': [],
        }
    except Exception as e:
        return {'error': str(e), 'syscalls': []}
