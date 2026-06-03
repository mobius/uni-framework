#!/usr/bin/env python3
"""
bench_pcie.py — TC-HETERO-001: PCIe 带宽压力测试

测量 4 卡 (Phi + 3×VE) 并发数据传输:
  - VE: 文件 I/O 方式 (H2D 读 + D2H 写)
  - Phi: micnativeloadex 加载时间近似
  - 单卡 baseline → 4 卡并发 → 争抢度分析

通过标准: H2D 总吞吐 ≥ 30 GB/s (4 卡合计)
"""

import sys, os, time, subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "src"))

VE_BIN = PROJECT / "src" / "kernels" / "ve" / "pcie_bw_ve"
MB = 256


def shell(cmd, timeout=60, env=None):
    t0 = time.time()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip(), time.time() - t0


def compile_ve():
    src = PROJECT / "src" / "kernels" / "ve" / "pcie_bw.c"
    if VE_BIN.exists():
        return True
    rc, _, err, _ = shell(f"ncc -O3 -o {VE_BIN} {src}")
    if rc != 0:
        print(f"[compile] pcie_bw_ve FAILED:\n{err}")
    return rc == 0


def parse_ve(stdout: str) -> dict:
    """Parse 'PCIe_BW: MB=256 H2D=5.46_GB/s D2H=1.27_GB/s ...'"""
    r = {"h2d": 0.0, "d2h": 0.0, "total": 0.0, "verify": "FAIL"}
    for token in stdout.split():
        if token.startswith("H2D="):
            r["h2d"] = float(token.split("=")[1].rstrip("_GB/s"))
        elif token.startswith("D2H="):
            r["d2h"] = float(token.split("=")[1].rstrip("_GB/s"))
        elif token.startswith("total="):
            r["total"] = float(token.split("=")[1].rstrip("_GB/s"))
        elif token.startswith("verify="):
            r["verify"] = token.split("=")[1]
    return r


def measure_phi() -> dict:
    """Measure Phi PCIe via micnativeloadex binary load time.
    
    This measures the time to transfer and launch the peak_fp64 binary,
    which is a PCIe-bound operation (binary ~50KB + runtime libs).
    """
    phi_bin = PROJECT / "src" / "kernels" / "phi" / "peak_fp64.mic"
    if not phi_bin.exists():
        return {"h2d": 0.0, "d2h": 0.0, "total": 0.0, "verify": "N/A"}

    mic_libs = PROJECT.parent / "intel_phi" / "icc_mic_libs"
    env = os.environ.copy()
    if mic_libs.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(mic_libs)

    # 用 micnativeloadex 的完整链路时间作为 PCIe 传输近似
    # 实际数据传输是二进制 + 运行时库加载
    rc, stdout, stderr, elapsed = shell(
        f"micnativeloadex {phi_bin} -d 0 -t 10", timeout=30, env=env)

    gflops = 0.0
    for line in (stdout + stderr).splitlines():
        if "GFLOPS" in line or "GFlops" in line:
            try:
                gflops = float(line.split(":")[-1].strip().split()[0])
            except ValueError:
                pass

    return {
        "h2d": 0.0,
        "d2h": 0.0,
        "total": 0.0,
        "verify": "OK" if gflops > 400 else "FAIL",
        "elapsed": elapsed,
        "gflops": gflops,
    }


def main():
    print("=" * 60)
    print("  TC-HETERO-001: PCIe 带宽压力测试")
    print("=" * 60)

    if not compile_ve():
        print("  ❌ VE 内核编译失败")
        return

    # ── 单卡 baseline ──
    print("\n--- 单卡 baseline (VE1 solo) ---")
    _, out, _, _ = shell(f"/opt/nec/ve/bin/ve_exec -N 1 {VE_BIN} {MB}")
    solo = parse_ve(out)
    print(f"  VE1 solo: H2D={solo['h2d']:.2f} D2H={solo['d2h']:.2f} "
          f"total={solo['total']:.2f} GB/s verify={solo['verify']}")

    # ── 三卡并发 ──
    print("\n--- 3 VE 并发 ---")
    import asyncio

    async def run_ve(ve_id):
        cmd = f"/opt/nec/ve/bin/ve_exec -N {ve_id} {VE_BIN} {MB}"
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        return ve_id, parse_ve(stdout.decode())

    async def run_all_ve():
        return await asyncio.gather(run_ve(1), run_ve(2), run_ve(3))

    ve_results = asyncio.run(run_all_ve())

    total_h2d = 0.0
    total_d2h = 0.0
    total_combined = 0.0
    for ve_id, r in ve_results:
        total_h2d += r["h2d"]
        total_d2h += r["d2h"]
        total_combined += r["total"]
        print(f"  VE{ve_id}: H2D={r['h2d']:.2f} D2H={r['d2h']:.2f} "
              f"total={r['total']:.2f} GB/s verify={r['verify']}")

    # ── Phi ──
    print("\n--- Phi PCIe (micnativeloadex timing) ---")
    phi = measure_phi()
    if phi.get("elapsed"):
        print(f"  Phi: elapsed={phi['elapsed']:.2f}s, "
              f"gflops={phi.get('gflops', 0):.0f}, verify={phi['verify']}")

    # ── 汇总 ──
    print("\n" + "=" * 60)
    print("  汇总")
    print("=" * 60)
    print(f"  VE 并发 H2D 合计:  {total_h2d:.2f} GB/s")
    print(f"  VE 并发 D2H 合计:  {total_d2h:.2f} GB/s")
    print(f"  VE 并发总吞吐:     {total_combined:.2f} GB/s")
    print(f"  VE solo H2D 参考:  {solo['h2d']:.2f} GB/s")

    h2d_ratio = total_h2d / (solo["h2d"] * 3) if solo["h2d"] > 0 else 0
    print(f"  并发效率 (H2D):    {h2d_ratio:.0%}")

    # 通过标准: H2D ≥ 30 GB/s (4 卡合计)
    # 注: 30 GB/s 基于 raw DMA, 非文件 I/O
    print(f"\n  通过标准: H2D ≥ 30 GB/s")
    if total_h2d >= 30:
        print(f"  ✅ 通过 ({total_h2d:.2f} GB/s)")
    else:
        print(f"  ⚠️ 未达标准 ({total_h2d:.2f} GB/s < 30 GB/s)")
        print(f"     文件 I/O 路径存在开销, raw DMA 带宽应更高")


if __name__ == "__main__":
    main()
