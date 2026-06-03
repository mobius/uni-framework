"""
调度层 — 设备发现与管理
devices.py — 扫描 Phi 和 VE 计算卡，返回 DeviceInfo 结构
"""

import subprocess
import json
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DeviceInfo:
    name: str              # "phi0", "ve1", "ve2", "ve3"
    kind: str              # "phi" | "ve"
    online: bool
    numa_node: int
    pcie_addr: str
    ve_id: Optional[int] = None   # VE 的 -N 编号 (1/2/3)

    # 运行时填充
    gflops: float = 0.0
    elapsed_sec: float = 0.0
    status: str = "pending"  # pending | running | pass | fail
    stdout: str = ""
    stderr: str = ""


def discover_phi() -> Optional[DeviceInfo]:
    """发现 Intel Xeon Phi 7120P"""
    try:
        result = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if "Xeon Phi" in line and "Co-processor" in line:
                addr = line.split()[0]
                numa = _get_numa(addr)
                return DeviceInfo(
                    name="phi0",
                    kind="phi",
                    online=True,
                    numa_node=numa,
                    pcie_addr=addr,
                )
        return None
    except Exception:
        return None


def discover_ve() -> list[DeviceInfo]:
    """发现 NEC VE 1.0 卡 (ve_exec -N 1/2/3)"""
    devices = []
    ve_path = "/sys/class/ve"
    if not os.path.isdir(ve_path):
        return devices

    for entry in sorted(os.listdir(ve_path)):
        if not entry.startswith("ve") or not entry[2:].isdigit():
            continue
        sysfs_id = int(entry[2:])  # ve0 → 0, ve1 → 1, ve2 → 2
        ve_n = sysfs_id + 1        # ve_exec -N 从 1 开始

        # 读取 PCIe 地址
        device_link = os.path.join(ve_path, entry, "device")
        pcie_addr = "unknown"
        if os.path.islink(device_link):
            pcie_addr = os.path.basename(os.readlink(device_link))

        # 测试是否可执行
        online = _test_ve_online(ve_n)

        numa = _get_numa(pcie_addr)

        devices.append(DeviceInfo(
            name=f"ve{ve_n}",
            kind="ve",
            online=online,
            numa_node=numa,
            pcie_addr=pcie_addr,
            ve_id=ve_n,
        ))

    return devices


def discover_all() -> list[DeviceInfo]:
    """发现全部加速器"""
    devices = []
    phi = discover_phi()
    if phi:
        devices.append(phi)
    devices.extend(discover_ve())
    return devices


def _test_ve_online(ve_id: int) -> bool:
    """测试 VE 卡是否可执行 (检查 sysfs 设备存在)"""
    ve_path = f"/sys/class/ve/ve{ve_id - 1}"
    if not os.path.isdir(ve_path):
        return False
    # VE 设备存在即视为可用 (ve_exec 需要 VE 编译的二进制)
    return True


def _get_numa(pcie_addr: str) -> int:
    """从 lspci 获取 NUMA node"""
    try:
        result = subprocess.run(
            ["lspci", "-vv", "-s", pcie_addr],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if "NUMA node" in line:
                return int(line.strip().split(":")[-1].strip())
    except Exception:
        pass
    return -1
