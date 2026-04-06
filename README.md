# KernelScope — Linux Kernel Internals Observatory

A live web dashboard that reads real kernel data structures through `/proc` and `/sys`
and visualises them — virtual address space layout, task_struct fields, VFS caches,
network stack, IPC demos, and the boot timeline.

---

## What each panel demonstrates

| Panel | Kernel concepts covered | Data source |
|-------|------------------------|-------------|
| Virtual Address Space Map | vm_area_struct, ASLR, 3GB/1GB split, vDSO | `/proc/<pid>/maps` |
| task_struct Inspector | pid, tgid, state, mm_struct, threads, files_struct | `/proc/<pid>/status`, `/proc/<pid>/task/` |
| Syscall Tracer | ring 3→0 transitions, SYSCALL instruction | `strace -c -p` |
| VFS Layer Monitor | inode cache, dentry cache, negative dentries, block I/O | `/proc/sys/fs/inode-nr`, `/proc/diskstats` |
| Network Stack Monitor | sk_buff, socket/sock, TCP states, protocol stats | `/proc/net/sockstat`, `/proc/net/snmp` |
| IPC & Demo Runner | mmap(), POSIX shm, zero-copy sendfile | Compiled C programs |
| Boot Timeline | UEFI→GRUB→kernel→PID1, dmesg ring buffer | `dmesg`, `systemd-analyze` |

---

## Prerequisites

You need a Linux machine (physical or VM). Ubuntu 22.04+ or Debian 12+ recommended.

```
sudo apt update
sudo apt install -y python3 python3-pip python3-venv gcc strace
```

---

## Step-by-step setup

### Step 1 — Clone / copy the project

```bash
# If you downloaded the zip, extract it:
cd ~
# (copy the kernelscope/ folder here)
cd kernelscope
```

### Step 2 — Create a Python virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3 — Install Python dependencies

```bash
pip install -r requirements.txt
```

### Step 4 — Allow strace to attach to other processes

By default the kernel blocks ptrace from attaching to unrelated processes.
Set the scope to 0 for this session:

```bash
echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope
```

> **Note:** This setting reverts on reboot. It is safe for a dev machine.
> Alternatively run KernelScope as root: `sudo venv/bin/python app.py`

### Step 5 — Run the app

```bash
python app.py
```

You will see:
```
[compile] OK mmap_demo
[compile] OK shm_ipc
[compile] OK zero_copy
 * Running on http://0.0.0.0:5000
```

### Step 6 — Open the dashboard

Open a browser and go to:
```
http://localhost:5000
```

If running on a remote VM:
```
http://<VM_IP>:5000
```

---

## Using the dashboard

### Attaching to a process

1. Type a PID in the PID box (top right), or pick one from the dropdown.
2. Click **Attach**.
3. Panel 1 (Memory Map) and Panel 2 (task_struct) refresh every 2 seconds.

**Good PIDs to try:**
```bash
# In another terminal:
sleep 1000 &         # Simple process — clean address space
echo $!              # Print its PID

# Or use a busy process:
yes > /dev/null &
echo $!

# Or your terminal emulator, a web browser, etc.
ps aux | grep python  # KernelScope itself is interesting to inspect
```

### Running the mmap demo

1. Click **Run mmap demo** in Panel 6.
2. Note the PID printed in the output box.
3. Attach that PID in the top bar.
4. Watch Panel 1 — you will see the `/tmp/ks_mmap_*` file appear as a VMA.
5. After 20 seconds the VMA vanishes (munmap called).

### Running the IPC demo

1. Click **Run IPC demo**.
2. The output shows parent + child PIDs and their different virtual addresses
   for the same physical shared memory.
3. While it runs: `ls /dev/shm/` — you will see `kernelscope_ipc_demo`.

### Tracing syscalls

1. Attach a PID.
2. Click **Trace 3s** in Panel 3.
3. The bar chart shows syscall frequency by category (file_io, memory, process, network, ipc).
4. Run the **zero-copy benchmark** and trace it — you will see sendfile() but NO read()/write().

### Boot timeline

Click **Load** in Panel 7 to fetch:
- Static boot stage diagram (UEFI → systemd)
- systemd-analyze blame (top slow services)
- Live dmesg ring buffer (first 60 lines from kernel startup)

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `strace: attach: ptrace(PTRACE_SEIZE, ...): Operation not permitted` | `echo 0 \| sudo tee /proc/sys/kernel/yama/ptrace_scope` |
| `Permission denied` reading `/proc/<pid>/maps` | Run as root, or attach only processes you own |
| `[compile] FAILED mmap_demo` | `sudo apt install gcc` |
| `flask_cors` not found | `pip install flask flask-cors` |
| `dmesg: read kernel buffer failed: Operation not permitted` | `sudo sysctl kernel.dmesg_restrict=0` or run as root |
| Port 5000 already in use | `python app.py` — edit the last line to change port |

---

## Interview talking points this project gives you

**On ASLR:**
> "I can show you live. Attach the same binary twice with different PIDs —
> the base addresses in Panel 1 change every time because the kernel randomises
> them via mmap_base randomisation in `do_mmap()` during `exec()`."

**On task_struct:**
> "TGID equals PID for the main thread. All threads share the same TGID.
> That maps directly to `task_struct.tgid` vs `task_struct.pid`.
> Panel 2 shows all TIDs under a multi-threaded process sharing the same TGID."

**On sk_buff:**
> "Every packet in flight is one `struct sk_buff`. The sockstat TCP.mem
> field in Panel 5 shows total pages held by TCP socket buffers system-wide.
> Under load that number rises — that is sk_buff memory pressure."

**On zero-copy:**
> "My benchmark shows sendfile at ~3x faster than read()+write() for 50MB.
> The syscall tracer confirms it: sendfile path shows zero read() or write() calls.
> The data went disk → page cache → socket buffer entirely in ring 0."

**On kernel space:**
> "The vDSO region at the top of every process's address space is kernel code
> mapped into user space. It lets gettimeofday() run without a full ring 3→ring 0
> transition. Panel 1 labels it purple — you can see it in every single process."

---

## Project structure

```
kernelscope/
├── app.py                     # Flask server, route handlers, demo runner
├── requirements.txt
├── collectors/
│   ├── __init__.py
│   ├── memory.py              # /proc/<pid>/maps parser (vm_area_struct)
│   ├── process.py             # /proc/<pid>/status (task_struct fields)
│   ├── syscall.py             # strace wrapper + categoriser
│   ├── vfs.py                 # inode/dentry cache + diskstats
│   ├── network.py             # sockstat, snmp, net/dev (sk_buff stats)
│   └── boot.py                # dmesg + systemd-analyze
├── demos/
│   ├── mmap_demo.c            # mmap a file, sleep 20s, munmap
│   ├── shm_ipc.c              # POSIX shm parent+child IPC
│   └── zero_copy.c            # sendfile vs read+write benchmark
└── static/
    └── index.html             # Full dashboard (Chart.js, no build step)
```
