#!/usr/bin/env python3
"""
bench_all.py — 全框架基准测试 + 性能预估对比

运行 basic → multi_task → pipeline，为每步做预估算力/时间，
然后与实际测量对比，输出统一统计报告。
"""

import sys, os, time, struct
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "src"))
from scheduler.profiler import Profiler

N = 512
MIC_LIBS = PROJECT.parent / "intel_phi" / "icc_mic_libs"
HDR = lambda s: print(f"\n{'='*60}\n  {s}\n{'='*60}")


# ═══════════════════════════════════════════════════════════════
# Shared utilities
# ═══════════════════════════════════════════════════════════════

def shell(cmd, env=None, timeout=120):
    import subprocess
    t0 = time.time()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, env=env or os.environ)
    return r.returncode, r.stdout, r.stderr, time.time() - t0


def compile_ve(name, src):
    k = PROJECT / "src" / "kernels" / "ve"
    exe = k / name
    if exe.exists(): return True
    rc, _, err, _ = shell(f"ncc -O3 -fopenmp -o {exe} {k / src}")
    if rc != 0: print(f"  [compile] {name} ❌\n{err}")
    return rc == 0


# ═══════════════════════════════════════════════════════════════
# Example 1: Basic — 4-card parallel FMA
# ═══════════════════════════════════════════════════════════════

def bench_basic(p: Profiler):
    HDR("Basic: 四卡 FMA 峰值")

    # Compile
    compile_ve("peak_fp64_ve", "peak_fp64.c")
    pk = PROJECT / "src" / "kernels" / "phi" / "peak_fp64.mic"
    if not pk.exists():
        shell("podman start centos7-phi-dev 2>/dev/null")
        shell(f"podman cp {PROJECT}/src/kernels/phi/peak_fp64.c centos7-phi-dev:/tmp/ && "
              f"podman exec centos7-phi-dev bash -c 'source /opt/intel/bin/compilervars.sh intel64 && "
              f"icc -std=c99 -mmic -O3 -openmp -o /tmp/peak_fp64.mic /tmp/peak_fp64.c' && "
              f"podman cp centos7-phi-dev:/tmp/peak_fp64.mic {pk}")

    env = os.environ.copy()
    if MIC_LIBS.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)

    tasks = [
        ("phi", "fma_peak", f"micnativeloadex {pk} -d 0 -t 60", env),
    ]
    for ve_id in [1, 2, 3]:
        exe = PROJECT / "src" / "kernels" / "ve" / "peak_fp64_ve"
        tasks.append(("ve", "fma_peak",
                      f"/opt/nec/ve/bin/ve_exec -N {ve_id} {exe}", None))

    for dev, op, cmd, e in tasks:
        est = p.estimate(dev, op, N=N)
        _, out, _, elapsed = shell(cmd, env=e)
        gflops = 0.0
        for line in out.splitlines():
            if "GFLOPS" in line:
                try: gflops = float(line.split(":")[-1].strip().split()[0])
                except: pass
        p.record(est, elapsed, gflops=gflops)
        print(f"  {dev:5s} {op:12s}: {gflops:.0f} GFLOPS in {elapsed:.2f}s")


# ═══════════════════════════════════════════════════════════════
# Example 2: Multi-Task — DAG with Phi+VE
# ═══════════════════════════════════════════════════════════════

def bench_multitask(p: Profiler):
    HDR("Multi-Task: DAG 分叉-汇合")
    import numpy as np

    wd = PROJECT / "examples" / "multi_task" / "run_data"
    wd.mkdir(exist_ok=True)
    kv = PROJECT / "src" / "kernels" / "ve"
    kp = PROJECT / "src" / "kernels" / "phi"

    compile_ve("matmul_block_ve", "matmul_block.c")
    compile_ve("aggregate_ve", "aggregate.c")
    pk = kp / "peak_fp64.mic"
    env = os.environ.copy()
    if MIC_LIBS.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)

    # ── gen ──
    t0 = time.time()
    rng = np.random.default_rng(42)
    for idx in range(3):
        A = rng.normal(0, 0.01, (N, N)).astype(np.float64)
        B = rng.normal(0, 0.01, (N, N)).astype(np.float64)
        data = struct.pack("i", N) + A.tobytes() + B.tobytes()
        (wd / f"input_{idx+1}.bin").write_bytes(data)
    elapsed = time.time() - t0
    e_gen = p.estimate("host", "gen", N=N)
    p.record(e_gen, elapsed)
    print(f"  gen (host): {elapsed:.2f}s")

    # ── dgemm × 3 ──
    for bid, ve_id in [(1, 1), (2, 2), (3, 3)]:
        est = p.estimate("ve", "dgemm", N=N)
        exe = kv / "matmul_block_ve"
        inp = wd / f"input_{bid}.bin"
        out = wd / f"result_{bid}.bin"
        _, stdout, _, elapsed = shell(
            f"/opt/nec/ve/bin/ve_exec -N {ve_id} {exe} {inp} {out}")
        gflops = 0.0
        for line in stdout.splitlines():
            if "GFLOPS" in line:
                try: gflops = float(line.split("GFLOPS")[0].split()[-1])
                except: pass
        p.record(est, elapsed, gflops=gflops)
        print(f"  dgemm_{bid} (ve{ve_id}): {gflops:.0f} GFLOPS in {elapsed:.2f}s")

    # ── aggregate ──
    est = p.estimate("ve", "aggregate", N=N)
    exe = kv / "aggregate_ve"
    _, _, _, elapsed = shell(
        f"/opt/nec/ve/bin/ve_exec -N 1 {exe} "
        f"{wd}/result_1.bin {wd}/result_2.bin {wd}/result_3.bin {wd}/final.bin")
    p.record(est, elapsed)
    print(f"  aggregate (ve1): {elapsed:.2f}s")

    # ── phi_peak ──
    est = p.estimate("phi", "fma_peak", N=N)
    _, out, _, elapsed = shell(f"micnativeloadex {pk} -d 0 -t 60", env=env)
    gflops = 0.0
    for line in out.splitlines():
        if "GFLOPS" in line:
            try: gflops = float(line.split(":")[-1].strip().split()[0])
            except: pass
    p.record(est, elapsed, gflops=gflops)
    print(f"  phi_peak: {gflops:.0f} GFLOPS in {elapsed:.2f}s")

    # ── stats ──
    est = p.estimate("host", "stats", N=N)
    t0 = time.time()
    with open(wd / "final.bin", "rb") as f:
        N_read = struct.unpack("i", f.read(4))[0]
        arr = np.frombuffer(f.read(), dtype=np.float64).reshape(N_read, N_read)
    _ = arr.min(), arr.max(), arr.mean(), arr.std()
    elapsed = time.time() - t0
    p.record(est, elapsed)
    print(f"  stats (host): {elapsed:.2f}s")


# ═══════════════════════════════════════════════════════════════
# Example 3: Pipeline — serial chain
# ═══════════════════════════════════════════════════════════════

def bench_pipeline(p: Profiler):
    HDR("Pipeline: 串行流水线 VE1→VE2→VE3→Phi→Host")
    import numpy as np

    wd = PROJECT / "examples" / "pipeline" / "run_data"
    wd.mkdir(exist_ok=True)
    kv = PROJECT / "src" / "kernels" / "ve"
    kp = PROJECT / "src" / "kernels" / "phi"

    compile_ve("matmul_block_ve", "matmul_block.c")
    compile_ve("scale_ve", "scale.c")
    compile_ve("transpose_ve", "transpose.c")
    pk = kp / "peak_fp64.mic"
    env = os.environ.copy()
    if MIC_LIBS.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)

    # ── gen ──
    t0 = time.time()
    rng = np.random.default_rng(42)
    A = rng.normal(0, 0.01, (N, N)).astype(np.float64)
    B = rng.normal(0, 0.01, (N, N)).astype(np.float64)
    data = struct.pack("i", N) + A.tobytes() + B.tobytes()
    (wd / "input.bin").write_bytes(data)
    elapsed = time.time() - t0
    e = p.estimate("host", "gen", N=N); p.record(e, elapsed)
    print(f"  gen (host): {elapsed:.2f}s")

    # ── dgemm (VE1) ──
    est = p.estimate("ve", "dgemm", N=N)
    _, stdout, _, elapsed = shell(
        f"/opt/nec/ve/bin/ve_exec -N 1 {kv}/matmul_block_ve {wd}/input.bin {wd}/c1.bin")
    gflops = 0.0
    for line in stdout.splitlines():
        if "GFLOPS" in line:
            try: gflops = float(line.split("GFLOPS")[0].split()[-1])
            except: pass
    p.record(est, elapsed, gflops=gflops)
    print(f"  dgemm (ve1): {gflops:.0f} GFLOPS in {elapsed:.2f}s")

    # ── scale (VE2) ──
    est = p.estimate("ve", "scale", N=N)
    _, _, _, elapsed = shell(
        f"/opt/nec/ve/bin/ve_exec -N 2 {kv}/scale_ve {wd}/c1.bin {wd}/c2.bin")
    p.record(est, elapsed)
    print(f"  scale (ve2): {elapsed:.2f}s")

    # ── transpose (VE3) ──
    est = p.estimate("ve", "transpose", N=N)
    _, _, _, elapsed = shell(
        f"/opt/nec/ve/bin/ve_exec -N 3 {kv}/transpose_ve {wd}/c2.bin {wd}/c3.bin")
    p.record(est, elapsed)
    print(f"  transpose (ve3): {elapsed:.2f}s")

    # ── stats (Phi + Host) ──
    est = p.estimate("phi", "fma_peak", N=N)
    _, out, _, elapsed = shell(f"micnativeloadex {pk} -d 0 -t 60", env=env)
    gflops = 0.0
    for line in out.splitlines():
        if "GFLOPS" in line:
            try: gflops = float(line.split(":")[-1].strip().split()[0])
            except: pass
    p.record(est, elapsed, gflops=gflops)
    print(f"  stats/phi_peak: {gflops:.0f} GFLOPS in {elapsed:.2f}s")

    # ── report (Host) ──
    est = p.estimate("host", "stats", N=N)
    t0 = time.time()
    with open(wd / "c3.bin", "rb") as f:
        N_read = struct.unpack("i", f.read(4))[0]
        arr = np.frombuffer(f.read(), dtype=np.float64).reshape(N_read, N_read)
    _ = arr.min(), arr.max(), arr.mean(), arr.std()
    elapsed = time.time() - t0
    p.record(est, elapsed)
    print(f"  report (host): {elapsed:.2f}s")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    profiler = Profiler()

    bench_basic(profiler)
    bench_multitask(profiler)
    bench_pipeline(profiler)

    HDR("全框架性能统计")
    print(profiler.report())
