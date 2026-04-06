"""
collectors/memory.py
Reads /proc/<pid>/maps and classifies each Virtual Memory Area (VMA).
Every line in /proc/pid/maps is one vm_area_struct in the kernel's mm_struct.
"""

REGION_COLORS = {
    'text':     '#378ADD',   # Executable code — .text section
    'data':     '#639922',   # Writable data — .data / .bss / anonymous rw
    'heap':     '#BA7517',   # Dynamic memory via brk()/malloc()
    'stack':    '#D85A30',   # Thread stack — grows downward
    'vdso':     '#7F77DD',   # Virtual Dynamic Shared Object — kernel pages in user space
    'vsyscall': '#534AB7',   # Legacy fast syscall page
    'mmap':     '#1D9E75',   # File-backed mmap() region
    'other':    '#888780',   # Everything else (anonymous, special)
}


def classify_region(perms, pathname):
    """
    Map a VMA's permissions + pathname to a semantic region type.

    Kernel uses vm_area_struct.vm_flags (VM_READ, VM_WRITE, VM_EXEC)
    and vm_area_struct.vm_file for the same classification internally.
    """
    if pathname == '[heap]':
        return 'heap'
    if pathname == '[stack]':
        return 'stack'
    if '[vdso]' in pathname:
        return 'vdso'
    if '[vsyscall]' in pathname:
        return 'vsyscall'
    if pathname.startswith('['):
        return 'other'
    if not pathname or pathname == '[anonymous]':
        # Anonymous — no file backing.
        # rw-p with no exec = data/bss or anonymous heap extension
        if 'w' in perms and 'x' not in perms:
            return 'data'
        return 'other'
    # File-backed mapping
    if 'x' in perms:
        return 'text'   # Executable shared library or binary segment
    if 'w' in perms:
        return 'data'   # Writable file mapping
    return 'mmap'       # Read-only file mapping


def get_memory_map(pid):
    """
    Parse /proc/<pid>/maps into a structured list of VMA entries.

    Each line format:
        addr_start-addr_end perms offset dev inode [pathname]

    This is a direct export of the process's vm_area_struct list,
    which the kernel traverses during page faults, exec(), fork(), etc.
    """
    maps_path = f'/proc/{pid}/maps'
    regions = []

    with open(maps_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 5)
            if len(parts) < 5:
                continue

            addr_range = parts[0]
            perms      = parts[1]
            offset     = parts[2]
            inode      = parts[4]
            pathname   = parts[5].strip() if len(parts) > 5 else ''

            start_hex, end_hex = addr_range.split('-')
            start_int = int(start_hex, 16)
            end_int   = int(end_hex, 16)
            size      = end_int - start_int

            region_type = classify_region(perms, pathname)

            regions.append({
                'start':       start_hex,
                'end':         end_hex,
                'start_int':   start_int,
                'end_int':     end_int,
                'size':        size,
                'size_kb':     round(size / 1024, 1),
                'perms':       perms,
                'offset':      offset,
                'inode':       inode,
                'pathname':    pathname or '[anonymous]',
                'region_type': region_type,
                'color':       REGION_COLORS.get(region_type, REGION_COLORS['other']),
            })

    # Compute relative positions (%) for the visual bar chart
    if regions:
        min_addr   = min(r['start_int'] for r in regions)
        max_addr   = max(r['end_int']   for r in regions)
        total_span = max(max_addr - min_addr, 1)

        for r in regions:
            r['pos_pct']   = (r['start_int'] - min_addr) / total_span * 100
            r['width_pct'] = max(r['size'] / total_span * 100, 0.15)

    # Group by type for the summary
    summary = {}
    for r in regions:
        t = r['region_type']
        if t not in summary:
            summary[t] = {'count': 0, 'total_kb': 0, 'color': r['color']}
        summary[t]['count']    += 1
        summary[t]['total_kb'] += r['size_kb']

    return {
        'pid':          pid,
        'regions':      regions,
        'total_vmas':   len(regions),
        'summary':      summary,
        'aslr_note':    (
            'ASLR (Address Space Layout Randomisation) randomises the base '
            'address of text, heap, stack, and mmap regions on each exec(). '
            'Restart this process and compare the hex addresses — they change.'
        ),
        'kernel_note':  (
            'On x86-64 Linux the kernel occupies the upper half of the 64-bit '
            'address space (0xFFFF800000000000 and above). It is mapped into '
            'every process\'s page tables but protected by hardware: any access '
            'from ring 3 raises a page fault. The vDSO region is a kernel page '
            'that appears at the top of user space — it lets gettimeofday() '
            'run without a full ring 3→ring 0 transition.'
        ),
    }
