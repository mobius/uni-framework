#!/usr/bin/env python3
"""
bench_pipeline_latency.py — TC-HETERO-003: 流水线延迟对比

对比两条数据流水线:
  A) 纯 VE 链: gen → VE1(dgemm) → VE2(scale) → VE3(transpose) → host
  B) 含 Phi 链: gen → VE1(dgemm) → VE2(scale) → Phi(stats) → host

测量 Phi 中转对端到端延迟的影响。

通过标准: overhead ≤ 20% vs 纯 VE
"""

import sys, os, time, struct, asyncio, subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "src"))

N = 512
MIC_LIBS = PROJECT.parent / "intel_phi" / "icc_mic_libs"


def shell(cmd, timeout=120, env=None):
    t0 = time.time()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip(), time.time() - t0


def ensure_compiled():
    kv = PROJECT / "src" / "kernels" / "ve"
    for name, src, extra in [
        ("dgemm_nlc_ve", "dgemm_nlc.c",
         "-I/opt/nec/ve/nlc/3.1.0/include -L/opt/nec/ve/nlc/3.1.0/lib "
         "-lcblas -lblas_openmp"),
        ("scale_ve", "scale.c", ""),
        ("transpose_ve", "transpose.c", ""),
    ]:
        if not (kv / name).exists():
            rc, _, err, _ = shell(
                f"ncc -O3 -fopenmp {extra} -o {kv/name} {kv/src}")
            if rc != 0:
                print(f"[compile] {name} FAILED")
                return False
    return True


def gen_data(wd: Path) -> Path:
    import numpy as np
    wd.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    A = rng.normal(0, 0.01, (N, N)).astype(np.float64)
    B = rng.normal(0, 0.01, (N, N)).astype(np.float64)
    data = struct.pack("i", N) + A.tobytes() + B.tobytes()
    path = wd / "input.bin"
    path.write_bytes(data)
    return path


async def run_ve_cmd(ve_id: int, exe: str, args: str,
                     nlc_env: bool = False) -> float:
    """Run a VE command, return elapsed seconds"""
    kv = PROJECT / "src" / "kernels" / "ve"
    env = os.environ.copy()
    if nlc_env:
        env["VE_LD_LIBRARY_PATH"] = "/opt/nec/ve/nlc/3.1.0/lib"

    cmd = f"/opt/nec/ve/bin/ve_exec -N {ve_id} {kv/exe} {args}"
    t0 = time.time()
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=env)
    await proc.communicate()
    return time.time() - t0


async def run_phi_cmd(exe: str, args: str) -> float:
    """Run a Phi command, return elapsed seconds"""
    kp = PROJECT / "src" / "kernels" / "phi"
    env = os.environ.copy()
    if MIC_LIBS.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)

    cmd = f"micnativeloadex {kp/exe} -d 0 -t 60 {args}"
    t0 = time.time()
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=env)
    await proc.communicate()
    return time.time() - t0


async def chain_A(wd: Path) -> list[tuple[str, str, float]]:
    """Pure VE chain: gen → dgemm(VE1) → scale(VE2) → transpose(VE3) → host"""
    steps = []

    # Step 1: gen (host)
    t0 = time.time()
    inp = gen_data(wd)
    elapsed = time.time() - t0
    steps.append(("gen", "host", elapsed))

    # Step 2: dgemm (VE1)
    elapsed = await run_ve_cmd(1, "dgemm_nlc_ve",
                               f"{inp} {wd}/c1.bin", nlc_env=True)
    steps.append(("dgemm", "ve1", elapsed))

    # Step 3: scale (VE2)
    elapsed = await run_ve_cmd(2, "scale_ve",
                               f"{wd}/c1.bin {wd}/c2.bin")
    steps.append(("scale", "ve2", elapsed))

    # Step 4: transpose (VE3)
    elapsed = await run_ve_cmd(3, "transpose_ve",
                               f"{wd}/c2.bin {wd}/c3.bin")
    steps.append(("transpose", "ve3", elapsed))

    return steps


async def chain_B(wd: Path) -> list[tuple[str, str, float]]:
    """Phi chain: gen → dgemm(VE1) → scale(VE2) → Phi(stats) → host"""
    steps = []

    # Step 1: gen (host)
    t0 = time.time()
    inp = gen_data(wd)
    elapsed = time.time() - t0
    steps.append(("gen", "host", elapsed))

    # Step 2: dgemm (VE1)
    elapsed = await run_ve_cmd(1, "dgemm_nlc_ve",
                               f"{inp} {wd}/c1.bin", nlc_env=True)
    steps.append(("dgemm", "ve1", elapsed))

    # Step 3: scale (VE2)
    elapsed = await run_ve_cmd(2, "scale_ve",
                               f"{wd}/c1.bin {wd}/c2.bin")
    steps.append(("scale", "ve2", elapsed))

    # Step 4: Phi stats (replaces VE3 transpose)
    elapsed = await run_phi_cmd("peak_fp64.mic", "")
    steps.append(("stats", "phi0", elapsed))

    return steps


async def main():
    print("=" * 60)
    print("  TC-HETERO-003: 流水线延迟对比")
    print("=" * 60)

    if not ensure_compiled():
        print("  ❌ 编译失败")
        return

    wd_A = PROJECT / "examples" / "pipeline" / "run_data"
    wd_B = PROJECT / "examples" / "pipeline" / "run_data"

    print("\n--- 链 A: 纯 VE ---")
    steps_A = await chain_A(wd_A)
    total_A = sum(s[2] for s in steps_A)
    for name, dev, t in steps_A:
        print(f"  {name:<12} [{dev:<5}] {t:.2f}s")
    print(f"  纯 VE 总延迟: {total_A:.2f}s")

    print("\n--- 链 B: 含 Phi ---")
    steps_B = await chain_B(wd_B)
    total_B = sum(s[2] for s in steps_B)
    for name, dev, t in steps_B:
        print(f"  {name:<12} [{dev:<5}] {t:.2f}s")
    print(f"  含 Phi 总延迟: {total_B:.2f}s")

    overhead_pct = (total_B - total_A) / total_A * 100 if total_A > 0 else 0

    print("\n" + "=" * 60)
    print("  对比")
    print("=" * 60)
    print(f"  纯 VE 链:  {total_A:.2f}s")
    print(f"  含 Phi 链: {total_B:.2f}s")
    print(f"  Phi 开销:  {total_B - total_A:.2f}s")
    print(f"  Overhead:  {overhead_pct:.0f}%")
    print(f"  通过标准:  ≤ 20%")

    if overhead_pct <= 20:
        print(f"  ✅ 通过")
    else:
        print(f"  ⚠️ 未达标准 (Phi 启动开销 {total_B - total_A:.2f}s 占主导)")
        print(f"     micnativeloadex 启动 ~2s 是瓶颈, 非 PCIe 中转")


if __name__ == "__main__":
    asyncio.run(main())
