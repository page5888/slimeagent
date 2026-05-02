"""System resource monitoring - CPU, RAM, Disk, Processes."""
import psutil
import time
from dataclasses import dataclass, field
from sentinel.config import CPU_WARN_PERCENT, RAM_WARN_PERCENT, DISK_WARN_PERCENT


@dataclass
class SystemSnapshot:
    timestamp: float
    cpu_percent: float
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disk_percent: float
    disk_free_gb: float
    top_processes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"CPU: {self.cpu_percent:.0f}% | RAM: {self.ram_percent:.0f}% ({self.ram_used_gb:.1f}/{self.ram_total_gb:.1f}GB) | Disk: {self.disk_percent:.0f}% (free: {self.disk_free_gb:.1f}GB)",
        ]
        if self.top_processes:
            lines.append("Top processes:")
            for p in self.top_processes[:5]:
                lines.append(f"  {p['name']} (PID {p['pid']}): CPU {p['cpu']:.0f}% | RAM {p['ram']:.0f}MB")
        return "\n".join(lines)


def get_top_processes(n=5):
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
        try:
            info = p.info
            ram_mb = info['memory_info'].rss / (1024 * 1024) if info['memory_info'] else 0
            procs.append({
                'pid': info['pid'],
                'name': info['name'],
                'cpu': info['cpu_percent'] or 0,
                'ram': ram_mb,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: x['cpu'] + x['ram'] / 100, reverse=True)
    return procs[:n]


def take_snapshot() -> SystemSnapshot:
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('D:/' if psutil.WINDOWS else '/')

    warnings = []
    if cpu > CPU_WARN_PERCENT:
        warnings.append(f"CPU usage critical: {cpu:.0f}%")
    if mem.percent > RAM_WARN_PERCENT:
        warnings.append(f"RAM usage critical: {mem.percent:.0f}%")
    if disk.percent > DISK_WARN_PERCENT:
        warnings.append(f"Disk usage critical: {disk.percent:.0f}%")

    return SystemSnapshot(
        timestamp=time.time(),
        cpu_percent=cpu,
        ram_percent=mem.percent,
        ram_used_gb=mem.used / (1024**3),
        ram_total_gb=mem.total / (1024**3),
        disk_percent=disk.percent,
        disk_free_gb=disk.free / (1024**3),
        top_processes=get_top_processes(),
        warnings=warnings,
    )
