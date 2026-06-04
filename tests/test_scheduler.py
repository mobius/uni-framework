"""
调度层单元测试

Usage:
  cd uni && ./env/.venv/bin/python3 -m pytest tests/ -v
  (或直接: ./env/.venv/bin/python3 tests/test_scheduler.py)
"""

import sys, os
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "src"))


# ── Device discovery ──

def test_device_discovery():
    """设备发现: 至少找到 Phi + 3×VE"""
    from scheduler.devices import discover_all
    devices = discover_all()
    kinds = [d.kind for d in devices]
    assert "phi" in kinds, f"Phi not found in {kinds}"
    assert kinds.count("ve") >= 3, f"Expected >=3 VE, got {kinds.count('ve')}"


def test_device_info():
    """DeviceInfo 字段完整性"""
    from scheduler.devices import discover_all
    for d in discover_all():
        assert d.name, "name is empty"
        assert d.kind in ("phi", "ve"), f"bad kind: {d.kind}"
        assert d.numa_node >= -1, f"bad numa_node: {d.numa_node}"
        assert d.online is not None


# ── NUMA binder ──

def test_numa_binder():
    """NUMABinder: 拓扑映射正确"""
    from scheduler.numa import get_binder
    binder = get_binder()
    assert binder.best_node("phi0") >= 0
    assert binder.best_node("ve1") >= 0
    assert binder.best_node("ve2") >= 0
    assert binder.best_node("ve3") >= 0
    assert binder.best_node("nonexistent") == -1

    # NUMA 绑定命令
    cmd = binder.bind_cmd("ve1")
    if binder.is_available():
        assert "numactl" in cmd
    else:
        assert cmd == ""


def test_numa_singleton():
    """NUMABinder 单例"""
    from scheduler.numa import get_binder, best_node, bind_cmd
    b1 = get_binder()
    b2 = get_binder()
    assert b1 is b2
    assert best_node("ve1") == b1.best_node("ve1")


# ── Power cap ──

def test_power_estimation():
    """功耗预估合理"""
    from scheduler.power import estimate_power
    assert 200 <= estimate_power("phi0", "fma_peak") <= 350
    assert 200 <= estimate_power("ve1", "dgemm") <= 350
    assert estimate_power("host", "idle") < 100
    assert estimate_power("unknown", "fma") > 0  # fallback


def test_power_cap_budget():
    """PowerCap 预算管理"""
    from scheduler.power import PowerCap
    cap = PowerCap(psu_limit_w=1600)
    assert cap.effective_limit == 1440.0
    assert cap.estimated_used_w == 0.0

    # 可以启动 3 VE + Phi
    assert cap.can_launch(["ve1", "ve2", "ve3", "phi0"],
                           {"ve1": "fma_peak", "ve2": "fma_peak",
                            "ve3": "fma_peak", "phi0": "fma_peak"})

    # 预留和释放
    cap.reserve(["phi0"], {"phi0": "fma_peak"})
    assert cap.estimated_used_w > 0
    cap.release(["phi0"])
    assert cap.estimated_used_w == 0.0

    # 超过预算 (需要指定 ops, 默认 idle=50W 无法触发)
    cap2 = PowerCap(psu_limit_w=500)
    assert not cap2.can_launch(["ve1", "ve2", "ve3"],
                                {"ve1": "fma_peak", "ve2": "fma_peak",
                                 "ve3": "fma_peak"})  # 3×280=840 > 450


def test_power_singleton():
    """PowerCap 单例"""
    from scheduler.power import get_cap
    c1 = get_cap(1600)
    c2 = get_cap(1600)
    assert c1 is c2


# ── TaskGraph ──

def test_task_node():
    """TaskNode 字段"""
    from scheduler.task_graph import TaskNode

    def noop():
        return {"status": "pass"}

    n = TaskNode("test", "ve1", noop, op="dgemm", estimated_watts=280)
    assert n.name == "test"
    assert n.device == "ve1"
    assert n.op == "dgemm"
    assert n.estimated_watts == 280
    assert n.status == "pending"


def test_task_graph_empty():
    """TaskGraph 空图"""
    from scheduler.task_graph import TaskGraph
    g = TaskGraph()
    assert g.estimated_total_power() == 0.0


def test_task_graph_power():
    """TaskGraph 功率估算"""
    from scheduler.task_graph import TaskGraph, TaskNode

    def noop():
        return {"status": "pass"}

    g = TaskGraph()
    g.add(TaskNode("a", "ve1", noop, estimated_watts=280))
    g.add(TaskNode("b", "ve2", noop, estimated_watts=150))
    assert g.estimated_total_power() == 430.0


# ── Profiler ──

def test_profiler_model():
    """Profiler 设备模型"""
    from scheduler.profiler import Profiler
    p = Profiler()
    est = p.estimate("ve", "dgemm", N=512)
    assert est.device == "ve"
    assert est.op == "dgemm"
    assert est.est_gflops > 0

    est2 = p.estimate("ve", "dgemm", N=512, use_nlc=True)
    assert est2.est_gflops > 0

    est3 = p.estimate("phi", "fma_peak", N=512)
    assert est3.est_gflops > 0


# ── Integration ──

def test_scheduler_init():
    """调度层 __init__.py 导出完整"""
    from scheduler import (
        DeviceInfo, discover_all,
        compile_phi_kernel, run_phi_kernel,
        compile_ve_kernel, run_ve_kernel,
        NUMABinder, get_binder,
        PowerCap, get_cap,
        TaskNode, TaskGraph,
        Profiler,
    )
    # 所有导出可用
    assert DeviceInfo is not None
    assert NUMABinder is not None
    assert PowerCap is not None
    assert TaskGraph is not None
    assert Profiler is not None


# ── Benchmarks API ──

def test_benchmark_imports():
    """基准测试封装可导入"""
    from benchmarks import run_pcie_bw, run_throughput, run_pipeline_latency
    assert callable(run_pcie_bw)
    assert callable(run_throughput)
    assert callable(run_pipeline_latency)


if __name__ == "__main__":
    # 手动运行所有测试
    tests = [
        test_device_discovery, test_device_info,
        test_numa_binder, test_numa_singleton,
        test_power_estimation, test_power_cap_budget, test_power_singleton,
        test_task_node, test_task_graph_empty, test_task_graph_power,
        test_profiler_model,
        test_scheduler_init, test_benchmark_imports,
    ]
    ok = 0
    for t in tests:
        try:
            t()
            print(f"✅ {t.__name__}")
            ok += 1
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
    print(f"\n{ok}/{len(tests)} passed")
