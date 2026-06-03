#!/usr/bin/env python3
"""
bench_throughput.py — TC-HETERO-002: 数据中心吞吐测试

4 卡 (Phi + 3×VE) 并行满载:
  - VE×3: NLC DGEMM N=2048
  - Phi: FMA 峰值
  - 汇总总算力，验证 ≥ 5.0 TFLOPS

Usage:
  ./env/.venv/bin/python3 scripts/bench_throughput.py
"""

import sys, os, time, struct, asyncio, subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "src"))

N = 2048
MIC_LIBS = PROJECT.parent / "intel_phi" / "icc_mic_libs"


def shell(cmd, timeout=120, env=None):
    t0 = time.time()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip(), time.time() - t0


def ensure_compiled():
    kv = PROJECT / "src" / "kernels" / "ve"
    dgemm_bin = kv / "dgemm_nlc_ve"
    if not dgemm_bin.exists():
        print("[compile] dgemm_nlc_ve ...")
        rc, _, err, _ = shell(
            f"ncc -O3 -fopenmp -o {dgemm_bin} {kv}/dgemm_nlc.c "
            f"-I/opt/nec/ve/nlc/3.1.0/include "
            f"-L/opt/nec/ve/nlc/3.1.0/lib -lcblas -lblas_openmp")
        if rc != 0:
            print(f"  FAILED:\n{err}")
            return False
    return True


def generate_matrices(wd: Path, count=3):
    import numpy as np
    wd.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    files = []
    for idx in range(count):
        A = rng.normal(0, 0.01, (N, N)).astype(np.float64)
        B = rng.normal(0, 0.01, (N, N)).astype(np.float64)
        data = struct.pack("i", N) + A.tobytes() + B.tobytes()
        path = wd / f"mat_{idx+1}.bin"
        path.write_bytes(data)
        files.append(path)
    return files


async def run_dgemm(ve_id: int, input_path: Path, output_path: Path) -> dict:
    kv = PROJECT / "src" / "kernels" / "ve"
    exe = kv / "dgemm_nlc_ve"
    nlc_env = os.environ.copy()
    nlc_env["VE_LD_LIBRARY_PATH"] = "/opt/nec/ve/nlc/3.1.0/lib"

    cmd = f"/opt/nec/ve/bin/ve_exec -N {ve_id} {exe} {input_path} {output_path}"
    t0 = time.time()
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=nlc_env)
    stdout, stderr = await proc.communicate()
    elapsed = time.time() - t0

    gflops = 0.0
    for line in (stdout.decode() + stderr.decode()).splitlines():
        if "GFLOPS" in line:
            try:
                gflops = float(line.split("GFLOPS")[0].split()[-1])
            except ValueError:
                pass
        if "GFLOPS" in line:
            try:
                gflops = float(line.split(":")[-1].strip().split()[0])
            except ValueError:
                pass

    return {"device": f"ve{ve_id}", "op": "dgemm", "gflops": gflops,
            "elapsed": elapsed, "status": "pass" if gflops > 500 else "fail"}


async def run_phi_fma() -> dict:
    phi_bin = PROJECT / "src" / "kernels" / "phi" / "peak_fp64.mic"
    env = os.environ.copy()
    if MIC_LIBS.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)

    cmd = f"micnativeloadex {phi_bin} -d 0 -t 60"
    t0 = time.time()
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=env)
    stdout, stderr = await proc.communicate()
    elapsed = time.time() - t0

    gflops = 0.0
    for line in (stdout.decode() + stderr.decode()).splitlines():
        if "GFLOPS" in line or "GFlops" in line:
            try:
                gflops = float(line.split(":")[-1].strip().split()[0])
            except ValueError:
                pass

    return {"device": "phi0", "op": "fma_peak", "gflops": gflops,
            "elapsed": elapsed, "status": "pass" if gflops > 400 else "fail"}


async def main():
    print("=" * 60)
    print("  TC-HETERO-002: 数据中心吞吐测试")
    print(f"  N={N}  |  目标: ≥ 5.0 TFLOPS")
    print("=" * 60)

    if not ensure_compiled():
        print("  ❌ 编译失败")
        return

    # 生成矩阵
    wd = PROJECT / "examples" / "throughput" / "run_data"
    print(f"\n[gen] 生成 3 对 {N}×{N} 随机矩阵...")
    t0 = time.time()
    mats = generate_matrices(wd, count=3)
    print(f"  完成 ({time.time() - t0:.1f}s) → {wd}")

    # 并行执行: 3×VE NLC DGEMM + Phi FMA
    print(f"\n[run] 并行启动 3×VE NLC DGEMM + Phi FMA...")
    t0 = time.time()

    tasks = [
        run_dgemm(1, mats[0], wd / "result_1.bin"),
        run_dgemm(2, mats[1], wd / "result_2.bin"),
        run_dgemm(3, mats[2], wd / "result_3.bin"),
        run_phi_fma(),
    ]
    results = await asyncio.gather(*tasks)
    total_elapsed = time.time() - t0

    # 汇总
    total_gflops = sum(r["gflops"] for r in results)
    total_tflops = total_gflops / 1000

    print(f"\n{'='*60}")
    print(f"  结果")
    print(f"{'='*60}")
    print(f"{'设备':<8} {'操作':<12} {'GFLOPS':<10} {'耗时':<8} {'状态'}")
    print(f"{'-'*50}")
    for r in results:
        print(f"{r['device']:<8} {r['op']:<12} {r['gflops']:<10.0f} "
              f"{r['elapsed']:<8.2f}s {'✅' if r['status'] == 'pass' else '❌'}")

    print(f"\n  总算力:      {total_gflops:.0f} GFLOPS = {total_tflops:.2f} TFLOPS")
    print(f"  总耗时:      {total_elapsed:.2f}s (并行)")
    print(f"  通过标准:    ≥ 5.00 TFLOPS (5000 GFLOPS)")

    if total_tflops >= 5.0:
        print(f"  ✅ 通过 ({total_tflops:.2f} TFLOPS)")
    else:
        print(f"  ❌ 未达标准 ({total_tflops:.2f} TFLOPS < 5.00)")


if __name__ == "__main__":
    asyncio.run(main())
