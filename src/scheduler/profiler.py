"""
性能分析器 — 预估算力/带宽/时间 + 实测对比
profiler.py

Usage:
    from scheduler.profiler import Profiler, DeviceModel, OpModel
    p = Profiler()
    est = p.estimate("ve", "dgemm", N=512)
    # ... run task ...
    actual = p.record("ve", "dgemm", elapsed=0.1, gflops=63.5)
    p.report()
"""

from dataclasses import dataclass, field
from typing import Optional


# ─── Device performance models ────────────────────────────────

@dataclass
class DeviceModel:
    """Known device performance characteristics (measured baselines)"""
    name: str
    fp64_peak_gflops: float       # theoretical
    fp64_measured_gflops: float    # actual (simple FMA loop)
    fp64_nlc_gflops: float         # actual (NLC BLAS, VE only)
    mem_bw_gbs: float              # measured device memory BW
    pcie_h2d_gbs: float            # measured PCIe Host→Device
    pcie_d2h_gbs: float            # measured PCIe Device→Host


# Calibrated from our measurements (see docs/research/)
DEVICE_MODELS = {
    "phi": DeviceModel(
        name="Phi 7120P",
        fp64_peak_gflops=1208,
        fp64_measured_gflops=575,
        fp64_nlc_gflops=575,        # same as measured (no NLC)
        mem_bw_gbs=157,
        pcie_h2d_gbs=10,
        pcie_d2h_gbs=5.3,
    ),
    "ve": DeviceModel(
        name="VE 1.0",
        fp64_peak_gflops=2160,
        fp64_measured_gflops=900,    # simple FMA (42%)
        fp64_nlc_gflops=1750,       # NLC BLAS (81%)
        mem_bw_gbs=1062,
        pcie_h2d_gbs=10,
        pcie_d2h_gbs=5.3,
    ),
    "host": DeviceModel(
        name="Xeon Gold 6252",
        fp64_peak_gflops=2400 * 0.3,  # 2×24 cores, ~30% efficiency
        fp64_measured_gflops=720,
        fp64_nlc_gflops=720,
        mem_bw_gbs=150,
        pcie_h2d_gbs=0,
        pcie_d2h_gbs=0,
    ),
}


# ─── Operation models ─────────────────────────────────────────

@dataclass
class OpEstimate:
    """Pre-run estimate for a task"""
    device: str
    op: str
    N: int                  # matrix size
    flops: float = 0.0      # total FLOPs
    bytes_rw: float = 0.0   # total bytes read+written
    est_time_s: float = 0.0
    est_gflops: float = 0.0
    est_bw_gbs: float = 0.0

    # Actuals (filled after execution)
    actual_time_s: float = 0.0
    actual_gflops: float = 0.0
    actual_bw_gbs: float = 0.0
    actual_status: str = ""


class Profiler:
    """Performance estimator and comparator"""

    def __init__(self):
        self.estimates: list[OpEstimate] = []
        self._models = DEVICE_MODELS

    def estimate(self, device: str, op: str, N: int = 512,
                 use_nlc: bool = False) -> OpEstimate:
        """Pre-run estimate based on device model and operation type"""
        model = self._models.get(device)
        if not model:
            model = self._models["host"]

        # Device GFLOPS for this op
        if use_nlc and device == "ve":
            dev_gflops = model.fp64_nlc_gflops
        else:
            dev_gflops = model.fp64_measured_gflops

        est = OpEstimate(device=device, op=op, N=N)

        if op == "dgemm":
            est.flops = 2.0 * N**3
            est.bytes_rw = 3.0 * N**2 * 8  # A+B+C
            # Naive DGEMM (ikj, no blocking) is memory-bound for small N
            # Empirical: ~65 GFLOPS on VE, ~7 GFLOPS on Phi for N=512
            naive_eff = 0.07  # 7% peak for naive triple-loop
            naive_gflops = dev_gflops * naive_eff
            est.est_time_s = est.flops / (naive_gflops * 1e9)
            est.est_gflops = dev_gflops  # show peak as reference

        elif op == "scale":
            est.flops = N**2
            est.bytes_rw = 2.0 * N**2 * 8  # read + write
            est.est_time_s = est.bytes_rw / (model.mem_bw_gbs * 1e9)
            est.est_bw_gbs = model.mem_bw_gbs

        elif op == "transpose":
            est.flops = 0
            est.bytes_rw = 2.0 * N**2 * 8
            est.est_time_s = est.bytes_rw / (model.mem_bw_gbs * 1e9)
            est.est_bw_gbs = model.mem_bw_gbs

        elif op == "fma_peak":
            est.flops = 1e12  # ~1 TFLOPS of work (rough)
            est.bytes_rw = 0
            # empirical timing: Phi ~2.5s, VE ~0.17s per run
            est.est_time_s = 1e12 / (dev_gflops * 1e9)  # FLOPs / GFLOPS
            est.est_gflops = dev_gflops

        elif op == "aggregate":
            est.flops = 3.0 * N**2
            est.bytes_rw = 4.0 * N**2 * 8
            est.est_time_s = est.bytes_rw / (model.mem_bw_gbs * 1e9)
            est.est_bw_gbs = model.mem_bw_gbs

        elif op == "stats":
            est.flops = 3.0 * N**2  # min+max+sum
            est.bytes_rw = N**2 * 8
            est.est_time_s = max(
                est.bytes_rw / (model.mem_bw_gbs * 1e9),
                est.flops / (dev_gflops * 1e9)
            )

        elif op == "gen":
            est.flops = 0
            est.bytes_rw = 2.0 * N**2 * 8  # A+B
            est.est_time_s = 0.1  # negligible

        else:
            est.est_time_s = 0.1  # unknown, assume fast

        self.estimates.append(est)
        return est

    def record(self, est: OpEstimate, elapsed_s: float,
               gflops: float = 0.0, bw_gbs: float = 0.0,
               status: str = "pass"):
        """Record actual measurements after execution"""
        est.actual_time_s = elapsed_s
        est.actual_gflops = gflops
        est.actual_bw_gbs = bw_gbs
        est.actual_status = status

    def report(self) -> str:
        """Generate comparison report"""
        lines = []
        lines.append("")
        lines.append(f"{'设备':<7} {'操作':<12} {'N':<6} "
                     f"{'预估算力':<10} {'实测算力':<10} {'效率':<7} "
                     f"{'预估时间':<9} {'实测时间':<9} {'状态'}")
        lines.append("-" * 85)

        for e in self.estimates:
            dev = e.device
            op = e.op
            n = str(e.N)

            est_gf = f"{e.est_gflops:.0f}" if e.est_gflops > 0 else "—"
            act_gf = f"{e.actual_gflops:.0f}" if e.actual_gflops > 0 else "—"

            if e.est_gflops > 0 and e.actual_gflops > 0:
                eff = f"{e.actual_gflops / e.est_gflops * 100:.0f}%"
            else:
                eff = "—"

            est_t = f"{e.est_time_s:.3f}s" if e.est_time_s > 0 else "—"
            act_t = f"{e.actual_time_s:.3f}s" if e.actual_time_s > 0 else "—"

            status = "✅" if e.actual_status == "pass" else "⚠️"

            lines.append(
                f"{dev:<7} {op:<12} {n:<6} "
                f"{est_gf:<10} {act_gf:<10} {eff:<7} "
                f"{est_t:<9} {act_t:<9} {status}"
            )

        lines.append("")
        return "\n".join(lines)
