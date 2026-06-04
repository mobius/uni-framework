"""
协同基准测试封装

Submodules:
    pcie_bw_stress         : TC-001 PCIe 带宽压力
    multi_device_throughput: TC-002 数据中心吞吐
    pipeline_latency       : TC-003 流水线延迟
"""

from .pcie_bw_stress import run as run_pcie_bw
from .multi_device_throughput import run as run_throughput
from .pipeline_latency import run as run_pipeline_latency
