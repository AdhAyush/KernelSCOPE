"""
collectors/network.py
Reads Linux network stack statistics from /proc/net/.

The Linux network stack is layered:
  Application → socket API (net/socket.c)
              → Protocol layer: TCP (net/ipv4/tcp.c) / UDP (net/ipv4/udp.c)
              → Network layer: IP (net/ipv4/ip_input.c / ip_output.c)
              → Driver layer: NIC driver via net_device ops

sk_buff (socket buffer) is the fundamental packet representation —
every packet in flight is one struct sk_buff (include/linux/skbuff.h).
sockstat counts show system-wide sk_buff memory pressure.
"""
import subprocess


def _read(path, default=''):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return default


def _parse_sockstat(text):
    """
    /proc/net/sockstat format:
        sockets: used 123
        TCP: inuse 45 orphan 2 tw 10 alloc 47 mem 123
        UDP: inuse 5 mem 4
    """
    result = {}
    for line in text.strip().split('\n'):
        parts = line.split()
        if not parts:
            continue
        proto = parts[0].rstrip(':')
        pairs = {}
        for i in range(1, len(parts) - 1, 2):
            try:
                pairs[parts[i]] = int(parts[i + 1])
            except (ValueError, IndexError):
                pass
        result[proto] = pairs
    return result


def _parse_snmp(text):
    """
    /proc/net/snmp format: header line followed by values line, repeated.
        Ip: Forwarding DefaultTTL ...
        Ip: 2 64 ...
    """
    result = {}
    lines = text.strip().split('\n')
    i = 0
    while i < len(lines) - 1:
        h = lines[i].split()
        v = lines[i + 1].split()
        if h and v and h[0] == v[0]:
            proto = h[0].rstrip(':')
            result[proto] = dict(zip(h[1:], v[1:]))
        i += 2
    return result


def _parse_net_dev(text):
    interfaces = []
    for line in text.strip().split('\n')[2:]:
        if ':' not in line:
            continue
        iface, stats = line.split(':', 1)
        p = stats.split()
        if len(p) < 16:
            continue
        interfaces.append({
            'name':       iface.strip(),
            'rx_bytes':   int(p[0]),
            'rx_packets': int(p[1]),
            'rx_errors':  int(p[2]),
            'rx_drops':   int(p[3]),
            'tx_bytes':   int(p[8]),
            'tx_packets': int(p[9]),
            'tx_errors':  int(p[10]),
            'tx_drops':   int(p[11]),
            'rx_mb':      round(int(p[0]) / 1024 / 1024, 2),
            'tx_mb':      round(int(p[8]) / 1024 / 1024, 2),
        })
    return interfaces


def _get_tcp_states():
    states = {
        'ESTABLISHED': 0, 'LISTEN': 0, 'TIME-WAIT': 0,
        'CLOSE-WAIT': 0,  'SYN-SENT': 0, 'FIN-WAIT-1': 0,
        'FIN-WAIT-2': 0,  'CLOSING': 0,  'LAST-ACK': 0,
    }
    try:
        result = subprocess.run(
            ['ss', '-tn', '--no-header'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split('\n'):
            p = line.split()
            if p:
                s = p[0]
                if s in states:
                    states[s] += 1
    except Exception:
        pass
    return {k: v for k, v in states.items() if v > 0 or k in ('ESTABLISHED', 'LISTEN')}


def get_network_stats():
    sockstat_raw = _parse_sockstat(_read('/proc/net/sockstat', ''))
    snmp         = _parse_snmp(_read('/proc/net/snmp', ''))
    interfaces   = _parse_net_dev(_read('/proc/net/dev', ''))
    tcp_states   = _get_tcp_states()

    tcp_snmp = snmp.get('Tcp', {})
    udp_snmp = snmp.get('Udp', {})
    ip_snmp  = snmp.get('Ip',  {})

    def _int(d, k):
        try:
            return int(d.get(k, 0))
        except Exception:
            return 0

    # Filter out loopback for cleaner display unless it's the only interface
    visible = [i for i in interfaces if i['name'] != 'lo']
    if not visible:
        visible = interfaces

    return {
        'sockstat': sockstat_raw,

        'tcp_states': tcp_states,

        'tcp_stats': {
            'curr_established': _int(tcp_snmp, 'CurrEstab'),
            'in_segs':          _int(tcp_snmp, 'InSegs'),
            'out_segs':         _int(tcp_snmp, 'OutSegs'),
            'retransmits':      _int(tcp_snmp, 'RetransSegs'),
            'in_errs':          _int(tcp_snmp, 'InErrs'),
            'attempt_fails':    _int(tcp_snmp, 'AttemptFails'),
            'estab_resets':     _int(tcp_snmp, 'EstabResets'),
        },

        'udp_stats': {
            'in_datagrams':    _int(udp_snmp, 'InDatagrams'),
            'out_datagrams':   _int(udp_snmp, 'OutDatagrams'),
            'in_errors':       _int(udp_snmp, 'InErrors'),
            'rcvbuf_errors':   _int(udp_snmp, 'RcvbufErrors'),
            'sndbuf_errors':   _int(udp_snmp, 'SndbufErrors'),
        },

        'ip_stats': {
            'in_delivers':     _int(ip_snmp, 'InDelivers'),
            'out_requests':    _int(ip_snmp, 'OutRequests'),
            'forwarded':       _int(ip_snmp, 'ForwDatagrams'),
            'in_discards':     _int(ip_snmp, 'InDiscards'),
        },

        'interfaces': visible,

        'layer_notes': {
            'socket_api':     'socket()/bind()/connect() → net/socket.c → sock_create()',
            'protocol_layer': 'TCP: net/ipv4/tcp_input.c  UDP: net/ipv4/udp.c',
            'network_layer':  'IP routing: net/ipv4/ip_input.c → ip_local_deliver()',
            'driver_layer':   'NIC driver → netif_receive_skb() → net/core/dev.c',
            'sk_buff': (
                'struct sk_buff (skbuff.h) = one packet. '
                'sockstat TCP.mem = sk_buff pages held by TCP sockets. '
                f'Currently: {sockstat_raw.get("TCP", {}).get("mem", "?")} pages.'
            ),
        },
    }
