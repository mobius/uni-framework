"""
功耗监控与封顶模块
power.py — 实时功耗读取 + 功率安全网

核心策略:
  - 1600W PSU 是硬约束 (满载 1730W 超过额定，不可同时满载)
  - 新任务启动前检查: estimated_total_power < 1600W
  - 超过阈值: 任务排队等待
  - 功耗来源: ipmitool (系统总功耗) + sysfs (VE) + 预估值 (Phi)

预估模型 (基于实测和 TDP):
  | 设备         | FMA峰值 | DGEMM | 轻量 ops | 空闲 |
  |-------------|--------|-------|----------|------|
  | Phi 7120P   | 280W   | 280W  | 150W     | 50W  |
  | VE 1.0      | 280W   | 280W  | 150W     | 50W  |
  | Host        | 300W   | 200W  | 100W     | 50W  |

Usage:
    from scheduler.power import PowerCap
    cap = PowerCap(psu_limit_w=1600)
    if cap.can_launch(["phi0", "ve1"], op_weights={"phi0": "fma", "ve1": "dgemm"}):
        cap.reserve(["phi0", "ve1"])
        # ... run ...
        cap.release(["phi0", "ve1"])
"""

import subprocess
import shutil
import time
from typing import Optional
from dataclasses import dataclass, field


# ─── Power estimation model ────────────────────────────────────

# Estimated watts per device per operation type
# Calibrated from TDP docs and practical measurements
_POWER_MODEL: dict[str, dict[str, float]] = {
    "phi": {
        "fma_peak":  280.0,
        "dgemm":     280.0,
        "scale":     150.0,
        "transpose": 150.0,
        "aggregate": 150.0,
        "stats":     150.0,
        "gen":        50.0,
        "idle":       50.0,
    },
    "ve": {
        "fma_peak":  280.0,
        "dgemm":     280.0,
        "scale":     150.0,
        "transpose": 150.0,
        "aggregate": 150.0,
        "stats":     150.0,
        "gen":        50.0,
        "idle":       50.0,
    },
    "host": {
        "fma_peak":  300.0,
        "dgemm":     200.0,
        "scale":     100.0,
        "transpose": 100.0,
        "aggregate": 100.0,
        "stats":     100.0,
        "gen":        50.0,
        "idle":       50.0,
    },
}


def estimate_power(device: str, op: str) -> float:
    """Estimate power draw (watts) for a device running a given operation.
    
    Args:
        device: "phi0", "ve1", "ve2", "ve3", or "host"
        op: Operation type like "fma_peak", "dgemm", "scale", "idle"
    
    Returns:
        Estimated watts
    """
    # Normalize device name to kind: strip trailing digits
    kind = "".join(c for c in device if not c.isdigit())
    if kind not in _POWER_MODEL:
        kind = "host"
    
    model = _POWER_MODEL[kind]
    return model.get(op, model.get("idle", 100.0))


# ─── Live power reading (best-effort) ──────────────────────────

def _read_ipmitool_power() -> Optional[float]:
    """Read system total power via ipmitool.
    
    Returns watts if available, None otherwise.
    """
    if not shutil.which("ipmitool"):
        return None
    
    try:
        result = subprocess.run(
            ["ipmitool", "sensor"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        
        total = 0.0
        found = False
        for line in result.stdout.splitlines():
            # Match lines like "PSU1 Power    | 350.000   | Watts"
            if "Power" in line and "Watts" in line:
                parts = line.split("|")
                if len(parts) >= 2:
                    try:
                        total += float(parts[1].strip())
                        found = True
                    except ValueError:
                        pass
        
        return total if found else None
    except Exception:
        return None


def _read_sysfs_power() -> Optional[float]:
    """Read system power from sysfs (if RAPL is available).
    
    Returns watts if available, None otherwise.
    """
    # Intel RAPL: /sys/class/powercap/intel-rapl:*/energy_uj
    import glob
    try:
        total_uj = 0.0
        found = False
        for entry in glob.glob("/sys/class/powercap/intel-rapl:*"):
            name_file = f"{entry}/name"
            energy_file = f"{entry}/energy_uj"
            try:
                with open(name_file) as f:
                    name = f.read().strip()
                # Skip sub-packages (core, uncore, dram), only read package
                if "package" not in name:
                    continue
                with open(energy_file) as f:
                    total_uj += float(f.read().strip())
                found = True
            except (IOError, ValueError):
                continue
        
        if not found:
            return None
        
        # RAPL gives cumulative energy in microjoules. We need instantaneous power.
        # We take two readings 100ms apart and compute delta.
        time.sleep(0.1)
        total_uj2 = 0.0
        for entry in glob.glob("/sys/class/powercap/intel-rapl:*"):
            name_file = f"{entry}/name"
            energy_file = f"{entry}/energy_uj"
            try:
                with open(name_file) as f:
                    name = f.read().strip()
                if "package" not in name:
                    continue
                with open(energy_file) as f:
                    total_uj2 += float(f.read().strip())
            except (IOError, ValueError):
                continue
        
        delta_uj = total_uj2 - total_uj
        if delta_uj <= 0:
            return None
        
        # watts = J/s, 1e6 uJ = 1 J
        return delta_uj / 1e6 / 0.1  # 100ms interval
    except Exception:
        return None


# ─── Power budget manager ──────────────────────────────────────

@dataclass
class _Reservation:
    """Track a running task's power allocation"""
    device: str
    op: str
    watts: float
    started_at: float = field(default_factory=time.time)


class PowerCap:
    """电源功率安全网
    
    管理 PSU 功率预算，防止同时满载导致过载。
    
    Usage:
        cap = PowerCap(psu_limit_w=1600)
        
        # Check if we can launch a task
        if cap.can_launch(["phi0", "ve1"], ops={"phi0": "fma", "ve1": "dgemm"}):
            cap.reserve(["phi0", "ve1"], ops={"phi0": "fma", "ve1": "dgemm"})
            # ... run tasks ...
            cap.release(["phi0", "ve1"])
    """
    
    def __init__(self, psu_limit_w: float = 1600.0,
                 safety_margin: float = 0.90):
        """Initialize power budget.
        
        Args:
            psu_limit_w: PSU rated power in watts (default: 1600)
            safety_margin: Fraction of PSU limit to use as effective cap (default: 0.90)
                          1600W * 0.90 = 1440W effective budget
        """
        self.psu_limit_w = psu_limit_w
        self.effective_limit = psu_limit_w * safety_margin
        self._reservations: dict[str, _Reservation] = {}
        self._queue: list[tuple[list[str], dict[str, str]]] = []
        self._ipmitool_available = shutil.which("ipmitool") is not None
    
    @property
    def budget_remaining_w(self) -> float:
        """Remaining power budget in watts"""
        used = sum(r.watts for r in self._reservations.values())
        return max(0.0, self.effective_limit - used)
    
    @property
    def estimated_used_w(self) -> float:
        """Currently estimated draw in watts"""
        return sum(r.watts for r in self._reservations.values())
    
    def get_live_power_w(self) -> Optional[float]:
        """Best-effort live system power reading.
        
        Returns watts or None if unavailable.
        """
        # Try ipmitool first (most accurate for total system)
        power = _read_ipmitool_power()
        if power is not None:
            return power
        
        # Try RAPL (CPU package only)
        power = _read_sysfs_power()
        if power is not None:
            return power
        
        return None
    
    def estimate_batch(self, devices: list[str],
                       ops: Optional[dict[str, str]] = None) -> float:
        """Estimate total watts for a batch of device+operation pairs.
        
        Args:
            devices: List of device names
            ops: Optional dict mapping device → operation. Uses "idle" if missing.
        
        Returns:
            Estimated total watts
        """
        if ops is None:
            ops = {}
        
        total = 0.0
        for dev in devices:
            op = ops.get(dev, "idle")
            total += estimate_power(dev, op)
        return total
    
    def can_launch(self, devices: list[str],
                   ops: Optional[dict[str, str]] = None) -> bool:
        """Check if launching tasks on given devices would stay within power budget.
        
        Args:
            devices: Device names to launch on
            ops: Optional operation map for power estimation
        
        Returns:
            True if within budget, False if would exceed
        """
        new_watts = self.estimate_batch(devices, ops)
        return (self.estimated_used_w + new_watts) <= self.effective_limit
    
    def reserve(self, devices: list[str],
                ops: Optional[dict[str, str]] = None):
        """Reserve power budget for a set of devices.
        
        Args:
            devices: Device names to reserve
            ops: Optional operation map
        """
        if ops is None:
            ops = {}
        
        for dev in devices:
            op = ops.get(dev, "idle")
            watts = estimate_power(dev, op)
            self._reservations[dev] = _Reservation(
                device=dev, op=op, watts=watts
            )
    
    def release(self, devices: list[str]):
        """Release power reservation for given devices.
        
        Args:
            devices: Device names to release
        """
        for dev in devices:
            self._reservations.pop(dev, None)
    
    def release_all(self):
        """Release all reservations"""
        self._reservations.clear()
    
    def status(self) -> str:
        """Human-readable power status"""
        live = self.get_live_power_w()
        live_str = f"{live:.0f}W" if live else "N/A"
        return (
            f"Power: {self.estimated_used_w:.0f}W estimated "
            f"/ {self.effective_limit:.0f}W limit "
            f"(live: {live_str}) "
            f"| {len(self._reservations)} devices reserved"
        )
    
    def __repr__(self) -> str:
        return self.status()


# ─── Module-level singleton ────────────────────────────────────

_default_cap: Optional[PowerCap] = None


def get_cap(psu_limit_w: float = 1600.0) -> PowerCap:
    """Get or create the default power cap singleton"""
    global _default_cap
    if _default_cap is None:
        _default_cap = PowerCap(psu_limit_w=psu_limit_w)
    return _default_cap
