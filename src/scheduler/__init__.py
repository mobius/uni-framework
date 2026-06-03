"""
uni-scheduler — Intel Phi 7120P + NEC VE 1.0×3 异构计算调度层

Submodules:
    devices     : Device discovery (Phi + VE)
    phi         : Phi compile/deploy/run
    ve          : VE compile/deploy/run
    numa        : NUMA affinity binding (Phase 2)
    power       : Power monitoring and capping (Phase 2)
    task_graph  : DAG task dependency scheduler
    profiler    : Performance estimation and comparison
"""

from .devices import DeviceInfo, discover_all, discover_phi, discover_ve
from .phi import compile_phi_kernel, run_phi_kernel
from .ve import compile_ve_kernel, run_ve_kernel
from .numa import NUMABinder, get_binder, bind_cmd, best_node
from .power import PowerCap, get_cap, estimate_power
from .task_graph import TaskNode, TaskGraph
from .profiler import Profiler, DeviceModel, OpEstimate
