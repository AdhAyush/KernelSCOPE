"""
collectors/vfs.py
Reads VFS (Virtual File System) statistics from /proc.

The VFS layer provides a uniform interface over different filesystem
implementations (ext4, xfs, tmpfs, procfs…). It maintains two key caches:
  - inode cache  (inode_hashtable in fs/inode.c)
  - dentry cache (dentry_hashtable in fs/dcache.c)

Both caches sit between system calls (open/stat/read) and the actual
filesystem drivers, avoiding repeated disk lookups for hot paths.
"""


def _read(path, default=''):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def get_vfs_stats():
    # ── Inode cache ───────────────────────────────────────────────────────────
    # /proc/sys/fs/inode-nr: <nr_inodes_in_use> <nr_free_inodes>
    # Kernel source: fs/inode.c  inode_stat
    inode_parts = _read('/proc/sys/fs/inode-nr').split()
    nr_inodes       = int(inode_parts[0]) if len(inode_parts) > 0 else 0
    nr_free_inodes  = int(inode_parts[1]) if len(inode_parts) > 1 else 0
    inode_hit_ratio = round((1 - nr_free_inodes / max(nr_inodes, 1)) * 100, 1)

    # ── Dentry cache ──────────────────────────────────────────────────────────
    # /proc/sys/fs/dentry-state:
    #   nr_dentry  nr_unused  age_limit  want_pages  nr_negative  dummy
    # Kernel source: fs/dcache.c  dentry_stat
    dentry_parts  = _read('/proc/sys/fs/dentry-state').split()
    nr_dentry     = int(dentry_parts[0]) if len(dentry_parts) > 0 else 0
    nr_unused_d   = int(dentry_parts[1]) if len(dentry_parts) > 1 else 0
    nr_negative   = int(dentry_parts[4]) if len(dentry_parts) > 4 else 0

    # ── Open file handles ─────────────────────────────────────────────────────
    # /proc/sys/fs/file-nr: <allocated> <free> <max>
    file_parts    = _read('/proc/sys/fs/file-nr').split()
    nr_open_files = int(file_parts[0]) if len(file_parts) > 0 else 0
    nr_max_files  = int(file_parts[2]) if len(file_parts) > 2 else 0

    # ── Block I/O (one entry per disk device) ─────────────────────────────────
    # /proc/diskstats fields (11 fields per device since Linux 2.6):
    #   major minor name reads_compl reads_merged sectors_read ms_reading
    #   writes_compl writes_merged sectors_written ms_writing
    #   ios_in_progress ms_doing_io ms_weighted_io
    # Kernel source: block/genhd.c  diskstats_show()
    disks = []
    try:
        with open('/proc/diskstats') as f:
            for line in f:
                p = line.split()
                if len(p) < 14:
                    continue
                name = p[2]
                # Skip loop/ram/dm devices and pure partitions for cleanliness
                if any(name.startswith(x) for x in ['loop', 'ram']):
                    continue
                disks.append({
                    'name':             name,
                    'reads_completed':  int(p[3]),
                    'sectors_read':     int(p[5]),
                    'mb_read':          round(int(p[5]) * 512 / 1024 / 1024, 1),
                    'ms_reading':       int(p[6]),
                    'writes_completed': int(p[7]),
                    'sectors_written':  int(p[9]),
                    'mb_written':       round(int(p[9]) * 512 / 1024 / 1024, 1),
                    'ms_writing':       int(p[10]),
                    'io_in_progress':   int(p[11]),
                    'ms_io_wait':       int(p[12]),
                })
    except Exception:
        pass

    # ── Page cache stats from /proc/meminfo ───────────────────────────────────
    meminfo = {}
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                k, _, v = line.partition(':')
                meminfo[k.strip()] = v.strip()
    except Exception:
        pass

    def _kb(key):
        return meminfo.get(key, '0 kB').replace(' kB', '').strip()

    return {
        'inodes': {
            'in_use':    nr_inodes,
            'free':      nr_free_inodes,
            'hit_ratio': inode_hit_ratio,
            'note': (
                'inode = index node. Stores file metadata (size, timestamps, '
                'permissions, pointer to data blocks). Each open() call '
                'walks dentries to find the inode. '
                'Kernel struct: struct inode (include/linux/fs.h)'
            ),
        },
        'dentry': {
            'total':    nr_dentry,
            'unused':   nr_unused_d,
            'negative': nr_negative,
            'note': (
                'dentry = directory entry. Maps filename → inode. '
                f'{nr_negative} negative dentries cache FAILED lookups — '
                'if you stat() a non-existent file twice, the second call '
                'hits the dentry cache and never touches the disk. '
                'Kernel struct: struct dentry (include/linux/dcache.h)'
            ),
        },
        'files': {
            'open':     nr_open_files,
            'max':      nr_max_files,
            'pct_used': round(nr_open_files / max(nr_max_files, 1) * 100, 2),
            'note':     'Each open fd = struct file * in the process files_struct.',
        },
        'disks': disks,
        'page_cache': {
            'total_kb':     _kb('MemTotal'),
            'cached_kb':    _kb('Cached'),
            'buffers_kb':   _kb('Buffers'),
            'available_kb': _kb('MemAvailable'),
            'dirty_kb':     _kb('Dirty'),
            'writeback_kb': _kb('Writeback'),
            'note': (
                'Cached = page cache (file data in RAM). '
                'Block I/O reads go: syscall → VFS → page cache → '
                'block device layer → disk driver. '
                'If the page is already cached, the disk is never touched.'
            ),
        },
    }
