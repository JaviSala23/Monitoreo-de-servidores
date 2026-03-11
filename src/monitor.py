"""
Recolección de métricas del servidor remoto vía un script Python
que se ejecuta en el servidor. Un único canal SSH por ciclo.
"""
import time
from dataclasses import dataclass, field
from typing import Optional

from .ssh_client import SSHClient

# Script Python que se envía al servidor y se ejecuta en su python3
_METRICS_SCRIPT = """
import time, shutil

def _rf(p):
    with open(p) as f:
        return f.read()

def _cpu():
    parts = list(map(int, _rf('/proc/stat').splitlines()[0].split()[1:8]))
    idle  = parts[3] + parts[4]       # idle + iowait
    total = sum(parts)
    return total - idle, total         # (active, total)

def _net():
    rx = tx = 0
    for line in _rf('/proc/net/dev').splitlines()[2:]:
        line = line.strip()
        if ':' not in line:
            continue
        iface = line.split(':')[0].strip()
        if iface == 'lo':
            continue
        fields = line.split(':')[1].split()
        if len(fields) >= 9:
            rx += int(fields[0])
            tx += int(fields[8])
    return rx, tx

# ---- primera muestra ----
c1 = _cpu()
n1 = _net()
time.sleep(1)
# ---- segunda muestra ----
c2 = _cpu()
n2 = _net()

# CPU %
cpu_pct = ((c2[0]-c1[0]) / float(c2[1]-c1[1])) * 100.0 if (c2[1]-c1[1]) > 0 else 0.0

# Memoria (MB)
mem = {}
for line in _rf('/proc/meminfo').splitlines():
    p = line.split()
    if len(p) >= 2:
        mem[p[0].rstrip(':')] = int(p[1])
mt = mem.get('MemTotal', 0) / 1024.0
ma = mem.get('MemAvailable', 0) / 1024.0
mu = mt - ma

# Disco (GB)
dk = shutil.disk_usage('/')
dg  = dk.total / 1073741824.0
dug = dk.used  / 1073741824.0
dp  = (dk.used / float(dk.total)) * 100.0 if dk.total > 0 else 0.0

# Red KB/s (1 segundo de ventana)
nrx = (n2[0] - n1[0]) / 1024.0
ntx = (n2[1] - n1[1]) / 1024.0

# Load average
la = _rf('/proc/loadavg').split()

# Uptime legible
try:
    import subprocess
    up = subprocess.check_output(['uptime','-p'], text=True).strip()
except Exception:
    up = "?"

print('CPU:%.2f'    % cpu_pct)
print('MEM:%.1f:%.1f' % (mt, mu))
print('DISK:%.2f:%.2f:%.1f' % (dg, dug, dp))
print('NET:%.3f:%.3f' % (nrx, ntx))
print('LOAD:%s:%s:%s' % (la[0], la[1], la[2]))
print('UPTIME:%s'    % up)
"""


@dataclass
class ServerMetrics:
    cpu_percent:   float = 0.0
    mem_percent:   float = 0.0
    mem_used_mb:   float = 0.0
    mem_total_mb:  float = 0.0
    disk_percent:  float = 0.0
    disk_used_gb:  float = 0.0
    disk_total_gb: float = 0.0
    net_rx_kbs:    float = 0.0   # KB/s descarga
    net_tx_kbs:    float = 0.0   # KB/s subida
    load_avg:      str   = ""
    uptime:        str   = ""
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class ServerMonitor:
    def __init__(self, ssh: SSHClient) -> None:
        self.ssh = ssh

    def collect(self) -> ServerMetrics:
        m = ServerMetrics()
        out, err = self.ssh.execute_python(_METRICS_SCRIPT, timeout=25)

        if not out:
            m.error = err[:120] if err else "Sin respuesta del servidor"
            return m

        try:
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("CPU:"):
                    m.cpu_percent = float(line[4:])

                elif line.startswith("MEM:"):
                    parts = line[4:].split(":")
                    m.mem_total_mb = float(parts[0])
                    m.mem_used_mb  = float(parts[1])
                    if m.mem_total_mb > 0:
                        m.mem_percent = (m.mem_used_mb / m.mem_total_mb) * 100.0

                elif line.startswith("DISK:"):
                    parts = line[5:].split(":")
                    m.disk_total_gb = float(parts[0])
                    m.disk_used_gb  = float(parts[1])
                    m.disk_percent  = float(parts[2])

                elif line.startswith("NET:"):
                    parts = line[4:].split(":")
                    m.net_rx_kbs = float(parts[0])
                    m.net_tx_kbs = float(parts[1])

                elif line.startswith("LOAD:"):
                    parts = line[5:].split(":")
                    m.load_avg = f"{parts[0]}, {parts[1]}, {parts[2]}"

                elif line.startswith("UPTIME:"):
                    m.uptime = line[7:]

        except Exception as e:
            m.error = f"Error de parseo: {e}"

        m.timestamp = time.time()
        return m
