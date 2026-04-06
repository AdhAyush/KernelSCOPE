import os
import subprocess
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS

from collectors import memory, process, syscall, vfs, network, boot

app = Flask(__name__, static_folder='static')
CORS(app)

DEMOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'demos')
_compiled = False


def compile_demos():
    global _compiled
    if _compiled:
        return
    demos = ['mmap_demo', 'shm_ipc', 'zero_copy']
    for demo in demos:
        src = os.path.join(DEMOS_DIR, f'{demo}.c')
        out = os.path.join(DEMOS_DIR, demo)
        if os.path.exists(src):
            result = subprocess.run(
                ['gcc', '-O2', '-o', out, src, '-lrt'],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"[compile] FAILED {demo}: {result.stderr}")
            else:
                print(f"[compile] OK {demo}")
    _compiled = True


# ── Static ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


# ── Process list ─────────────────────────────────────────────────────────────

@app.route('/api/processes')
def get_processes():
    procs = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/comm') as f:
                name = f.read().strip()
            procs.append({'pid': int(pid), 'name': name})
        except Exception:
            pass
    procs.sort(key=lambda x: x['pid'])
    return jsonify(procs)


# ── Per-process endpoints ────────────────────────────────────────────────────

@app.route('/api/process/<int:pid>/maps')
def get_maps(pid):
    try:
        return jsonify(memory.get_memory_map(pid))
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/process/<int:pid>/info')
def get_info(pid):
    try:
        return jsonify(process.get_process_info(pid))
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/process/<int:pid>/syscalls')
def get_syscalls(pid):
    try:
        return jsonify(syscall.trace_syscalls(pid))
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ── System-wide endpoints ────────────────────────────────────────────────────

@app.route('/api/vfs')
def get_vfs():
    try:
        return jsonify(vfs.get_vfs_stats())
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/network')
def get_network():
    try:
        return jsonify(network.get_network_stats())
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/boot')
def get_boot():
    try:
        return jsonify(boot.get_boot_info())
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ── Demo runners ─────────────────────────────────────────────────────────────

@app.route('/api/demo/mmap', methods=['POST'])
def run_mmap():
    binary = os.path.join(DEMOS_DIR, 'mmap_demo')
    if not os.path.exists(binary):
        return jsonify({'error': 'Binary not compiled. Check server logs.'}), 500
    proc = subprocess.Popen(
        [binary], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    lines = []
    for _ in range(10):
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line.rstrip())
    return jsonify({'output': '\n'.join(lines), 'demo_pid': proc.pid, 'status': 'running (sleeping 20s)'})


@app.route('/api/demo/ipc', methods=['POST'])
def run_ipc():
    binary = os.path.join(DEMOS_DIR, 'shm_ipc')
    if not os.path.exists(binary):
        return jsonify({'error': 'Binary not compiled.'}), 500
    try:
        result = subprocess.run([binary], capture_output=True, text=True, timeout=20)
        return jsonify({'output': result.stdout + result.stderr, 'status': 'done'})
    except subprocess.TimeoutExpired:
        return jsonify({'output': 'Timed out', 'status': 'timeout'})


@app.route('/api/demo/zerocopy', methods=['POST'])
def run_zerocopy():
    binary = os.path.join(DEMOS_DIR, 'zero_copy')
    if not os.path.exists(binary):
        return jsonify({'error': 'Binary not compiled.'}), 500
    try:
        result = subprocess.run([binary], capture_output=True, text=True, timeout=60)
        return jsonify({'output': result.stdout + result.stderr, 'status': 'done'})
    except subprocess.TimeoutExpired:
        return jsonify({'output': 'Timed out after 60s', 'status': 'timeout'})


if __name__ == '__main__':
    compile_demos()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
