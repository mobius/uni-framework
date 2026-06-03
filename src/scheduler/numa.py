"""
NUMA 亲和绑定模块
numa.py — 为加速器任务提供 NUMA node 绑定

核心策略:
  - VE0 → NUMA0, VE1/VE2 → NUMA1 (根据实际拓扑)
  - Phi  → 根据物理 Slot 对应的 NUMA node
  - 绑定方式: numactl --cpunodebind=N --membind=N

Usage:
    from scheduler.numa import NUMABinder
    binder = NUMABinder()
    cmd = binder.bind("ve1")       # → "numactl --cpunodebind=0 --membind=0 "
    cmd = binder.bind("phi0")      # → "numactl --cpunodebind=0 --membind=0 "
    cmd = binder.bind("unknown")   # → ""
"""

import subprocess
import shutil
from typing import Optional

from .devices import DeviceInfo, discover_all


# ─── NUMA topology (discovered at import time) ──────────────────

def _discover_numa_map() -> dict[str, int]:
    """Discover actual NUMA mapping for all devices.
    
    Returns dict[name] → numa_node.
    Falls back to known defaults if discovery fails.
    """
    mapping: dict[str, int] = {}
    
    try:
        devices = discover_all()
        for d in devices:
            if d.online and d.numa_node >= 0:
                mapping[d.name] = d.numa_node
    except Exception:
        pass
    
    return mapping


# Known defaults (ES4000 G4 topology, verified 2026-06-03)
_DEFAULT_NUMA_MAP: dict[str, int] = {
    "phi0": 0,
    "ve1":  0,   # VE0 → NUMA0
    "ve2":  1,   # VE1 → NUMA1
    "ve3":  1,   # VE2 → NUMA1
}


class NUMABinder:
    """NUMA 亲和绑定器
    
    Provides:
      - bind_cmd(device)  → numactl prefix string (empty if no binding needed)
      - best_node(device) → recommended NUMA node
      - rebind()          → re-discover topology
    """
    
    def __init__(self):
        self._map: dict[str, int] = {}
        self._numactl_available: bool = shutil.which("numactl") is not None
        self.rebind()
    
    def rebind(self):
        """Re-discover NUMA topology (call after hardware changes)"""
        discovered = _discover_numa_map()
        self._map = {**_DEFAULT_NUMA_MAP, **discovered}
    
    def best_node(self, device: str) -> int:
        """Return the optimal NUMA node for a device.
        
        Args:
            device: Device name like "phi0", "ve1", "ve2", "ve3"
        
        Returns:
            NUMA node index, or -1 if unknown
        """
        return self._map.get(device, -1)
    
    def bind_cmd(self, device: str) -> str:
        """Return a numactl prefix command for binding to the device's NUMA node.
        
        Args:
            device: Device name
        
        Returns:
            Prefix string like "numactl --cpunodebind=0 --membind=0 ",
            or empty string if numactl unavailable or node unknown.
        """
        if not self._numactl_available:
            return ""
        
        node = self.best_node(device)
        if node < 0:
            return ""
        
        return f"numactl --cpunodebind={node} --membind={node} "
    
    def bind(self, device: str) -> str:
        """Alias for bind_cmd"""
        return self.bind_cmd(device)
    
    def is_available(self) -> bool:
        """Whether numactl is available on this system"""
        return self._numactl_available


# ─── Module-level singleton ────────────────────────────────────

_default_binder: Optional[NUMABinder] = None


def get_binder() -> NUMABinder:
    """Get or create the default NUMA binder singleton"""
    global _default_binder
    if _default_binder is None:
        _default_binder = NUMABinder()
    return _default_binder


def bind_cmd(device: str) -> str:
    """Convenience: get numactl prefix for a device"""
    return get_binder().bind_cmd(device)


def best_node(device: str) -> int:
    """Convenience: get best NUMA node for a device"""
    return get_binder().best_node(device)
