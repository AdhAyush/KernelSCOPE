"""
collectors/boot.py
Reads boot timeline from dmesg and systemd-analyze.

Boot sequence:
  1. UEFI/BIOS firmware → POST, find bootloader
  2. GRUB2            → loads bzImage + initrd from disk
  3. Kernel decompress → arch/x86/boot/compressed/head_64.S
  4. start_kernel()   → init/main.c — scheduler, memory zones, interrupts
  5. initramfs        → temporary root, loads storage drivers
  6. switch_root      → mount real root filesystem
  7. PID 1 (systemd)  → /sbin/init → parallel service activation
"""
import subprocess
import re


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ''


def _parse_time_ms(s):
    """Parse systemd time strings like '1.234s', '234ms', '1min 5.234s'."""
    s = s.strip()
    try:
        m = re.match(r'(\d+)min\s+(\d+(?:\.\d+)?)s', s)
        if m:
            return int(float(m.group(1)) * 60000 + float(m.group(2)) * 1000)
        if s.endswith('ms'):
            return round(float(s[:-2]))
        if s.endswith('s'):
            return round(float(s[:-1]) * 1000)
    except Exception:
        pass
    return 0


def get_boot_info():
    # ── dmesg ──────────────────────────────────────────────────────────────────
    dmesg_lines = []
    try:
        out = _run(['dmesg', '--time-format', 'reltime', '--color=never'])
        if not out:
            # Fallback without time format flag (older kernels)
            out = _run(['dmesg'])
        for line in out.split('\n')[:100]:
            if line.strip():
                dmesg_lines.append(line.rstrip())
    except Exception:
        dmesg_lines = ['dmesg unavailable (may need root or CAP_SYSLOG)']

    # ── systemd-analyze blame ──────────────────────────────────────────────────
    blame = []
    blame_raw = _run(['systemd-analyze', 'blame', '--no-pager'], timeout=15)
    for line in blame_raw.strip().split('\n')[:40]:
        p = line.strip().split(None, 1)
        if len(p) == 2:
            ms = _parse_time_ms(p[0])
            blame.append({
                'service':  p[1].strip(),
                'time_ms':  ms,
                'time_str': p[0].strip(),
            })
    # Sort descending by time
    blame.sort(key=lambda x: x['time_ms'], reverse=True)

    # ── systemd-analyze critical-chain ─────────────────────────────────────────
    critical_chain = _run(
        ['systemd-analyze', 'critical-chain', '--no-pager'], timeout=10
    ).strip()

    # ── Total boot time ────────────────────────────────────────────────────────
    boot_time_line = _run(['systemd-analyze', '--no-pager'], timeout=5)
    boot_time = boot_time_line.strip().split('\n')[0] if boot_time_line else ''

    return {
        'dmesg':          dmesg_lines,
        'blame':          blame,
        'critical_chain': critical_chain,
        'boot_time':      boot_time,
        'boot_stages': [
            {
                'stage': 'UEFI / BIOS',
                'desc':  'Power-on self-test, initialise hardware, hand off to bootloader',
                'source': 'Firmware (not Linux)',
            },
            {
                'stage': 'GRUB2',
                'desc':  'Loads compressed kernel image (bzImage) and initramfs into RAM',
                'source': 'boot/grub/grub.cfg',
            },
            {
                'stage': 'Kernel decompression',
                'desc':  'bzImage decompresses itself. setup_arch() initialises CPU and memory map',
                'source': 'arch/x86/boot/compressed/head_64.S',
            },
            {
                'stage': 'start_kernel()',
                'desc':  'Sets up scheduler, IRQs, memory zones, VFS, network stack',
                'source': 'init/main.c  start_kernel()',
            },
            {
                'stage': 'initramfs',
                'desc':  'Temporary root filesystem. Loads storage/filesystem drivers, finds real root',
                'source': 'usr/gen_init_cpio.c  +  init/initramfs.c',
            },
            {
                'stage': 'PID 1 — systemd',
                'desc':  'Mounts real root. Reads unit files. Activates services in dependency order',
                'source': '/lib/systemd/systemd',
            },
        ],
    }
