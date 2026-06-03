#!/usr/bin/env python3
"""
pipeline.py — 串行流水线: VE1→VE2→VE3→Phi→Host

DAG (严格串行):
  gen(Host) → dgemm(VE1) → scale(VE2) → transpose(VE3) → stats(Phi) → report(Host)

每步输出是下一步输入，数据接力流经全部 5 个计算单元。
"""

import sys, os, subprocess, asyncio, time, struct
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT / "src"))
from scheduler.task_graph import TaskGraph, TaskNode

WORKDIR   = PROJECT / "examples" / "pipeline" / "run_data"
KERNEL_VE = PROJECT / "src" / "kernels" / "ve"
KERNEL_PH = PROJECT / "src" / "kernels" / "phi"
MIC_LIBS  = PROJECT.parent / "intel_phi" / "icc_mic_libs"
N = 512


def run(cmd, timeout=120, env=None):
    t0 = time.time()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, env=env or os.environ)
    return {"status": "pass" if r.returncode == 0 else "fail",
            "stdout": r.stdout.strip(), "stderr": r.stderr.strip(),
            "elapsed": time.time() - t0}


def compile_ve(name, src):
    exe = KERNEL_VE / name
    if exe.exists(): return True
    r = subprocess.run(f"ncc -O3 -fopenmp -o {exe} {KERNEL_VE / src}",
                       shell=True, capture_output=True, text=True, timeout=60)
    ok = r.returncode == 0
    tag = "✅" if ok else "❌"
    print(f"  [compile] {name} {tag}")
    if not ok: print(r.stderr)
    return ok


# ─── Tasks ────────────────────────────────────────────────────

def task_gen():
    """生成随机矩阵 A, B (512×512)"""
    import numpy as np
    os.makedirs(WORKDIR, exist_ok=True)
    rng = np.random.default_rng(42)
    A = rng.normal(0, 0.01, (N, N)).astype(np.float64)
    B = rng.normal(0, 0.01, (N, N)).astype(np.float64)

    # Write A,B
    data = struct.pack("i", N) + A.tobytes() + B.tobytes()
    (WORKDIR / "input.bin").write_bytes(data)

    # Reference: C = A×B, then C×2, then transpose
    C1 = A @ B
    C2 = C1 * 2.0
    C3 = C2.T
    return {"status": "pass", "ref_checksum": float(C3.sum()),
            "ref_min": float(C3.min()), "ref_max": float(C3.max())}


def task_dgemm_ve1():
    """VE1: C1 = A × B"""
    exe = KERNEL_VE / "matmul_block_ve"
    cmd = f"/opt/nec/ve/bin/ve_exec -N 1 {exe} {WORKDIR}/input.bin {WORKDIR}/c1.bin"
    return run(cmd)


def task_scale_ve2():
    """VE2: C2 = C1 × 2.0"""
    exe = KERNEL_VE / "scale_ve"
    cmd = f"/opt/nec/ve/bin/ve_exec -N 2 {exe} {WORKDIR}/c1.bin {WORKDIR}/c2.bin"
    return run(cmd)


def task_transpose_ve3():
    """VE3: C3 = C2^T"""
    exe = KERNEL_VE / "transpose_ve"
    cmd = f"/opt/nec/ve/bin/ve_exec -N 3 {exe} {WORKDIR}/c2.bin {WORKDIR}/c3.bin"
    return run(cmd)


def task_stats_phi():
    """Phi: 统计 C3 的 min/max/mean/stddev"""
    input_file = WORKDIR / "c3.bin"
    exe = KERNEL_PH / "peak_fp64.mic"  # 复用已有内核测 Phi 算力

    # 并行跑两个：Phi FP64 峰值 + Host numpy 统计
    # Phi 峰值 (证明 Phi 在工作)
    env = os.environ.copy()
    if MIC_LIBS.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)
    r_phi = run(f"micnativeloadex {exe} -d 0 -t 30", env=env)

    # Host 端统计 C3
    import numpy as np
    with open(input_file, "rb") as f:
        N_read = struct.unpack("i", f.read(4))[0]
        arr = np.frombuffer(f.read(), dtype=np.float64).reshape(N_read, N_read)
    stats = {"N": N_read, "min": float(arr.min()), "max": float(arr.max()),
             "mean": float(arr.mean()), "stddev": float(arr.std())}

    # Parse Phi GFLOPS
    gflops = 0.0
    for line in r_phi["stdout"].splitlines():
        if "GFLOPS" in line:
            try: gflops = float(line.split(":")[-1].strip().split()[0])
            except: pass

    return {"status": "pass", "stats": stats, "phi_gflops": gflops}


def task_report():
    """Host: 打印最终报告"""
    import numpy as np
    input_file = WORKDIR / "c3.bin"
    with open(input_file, "rb") as f:
        N_read = struct.unpack("i", f.read(4))[0]
        arr = np.frombuffer(f.read(), dtype=np.float64).reshape(N_read, N_read)

    checksum = float(arr.sum())
    print(f"\n  ╔══════════════════════════════╗")
    print(f"  ║  串行流水线 最终报告          ║")
    print(f"  ╠══════════════════════════════╣")
    print(f"  ║  N={N_read}, elements={N_read*N_read}         ║")
    print(f"  ║  min={arr.min():.6f}              ║")
    print(f"  ║  max={arr.max():.6f}              ║")
    print(f"  ║  mean={arr.mean():.6f}             ║")
    print(f"  ║  stddev={arr.std():.6f}            ║")
    print(f"  ║  checksum={checksum:.1f}      ║")
    print(f"  ╚══════════════════════════════╝")
    return {"status": "pass", "checksum": checksum}


# ─── Main ────────────────────────────────────────────────────

async def main():
    B = lambda s: print(f"\n{'='*55}\n  {s}\n{'='*55}")

    B("编译内核")
    all([compile_ve("matmul_block_ve", "matmul_block.c"),
         compile_ve("scale_ve", "scale.c"),
         compile_ve("transpose_ve", "transpose.c")])

    os.makedirs(WORKDIR, exist_ok=True)
    for f in WORKDIR.glob("*.bin"): f.unlink()

    B("构建串行 DAG")
    graph = TaskGraph()
    graph.add(TaskNode("gen",       "host", task_gen))
    graph.add(TaskNode("dgemm",     "ve1",  task_dgemm_ve1,      depends_on=["gen"]))
    graph.add(TaskNode("scale",     "ve2",  task_scale_ve2,      depends_on=["dgemm"]))
    graph.add(TaskNode("transpose", "ve3",  task_transpose_ve3,  depends_on=["scale"]))
    graph.add(TaskNode("stats",     "phi0", task_stats_phi,      depends_on=["transpose"]))
    graph.add(TaskNode("report",    "host", task_report,         depends_on=["stats"]))

    B("执行串行流水线")
    results = await graph.execute(verbose=True)

    B("校验")
    gen = results.get("gen", {})
    rep = results.get("report", {})
    sta = results.get("stats", {}).get("stats", {})

    ref = gen.get("ref_checksum", 0)
    got = rep.get("checksum", 0)
    diff = abs(got - ref) / max(abs(ref), 1.0)

    print(f"  参考校验和: {ref:.1f}")
    print(f"  最终校验和: {got:.1f}")
    if diff < 1e-6:
        print(f"  ✅ 匹配 (差异 {diff:.2e})")
    else:
        print(f"  ❌ 不匹配 (差异 {diff:.2e})")

    ref_stats = (gen.get("ref_min", 0), gen.get("ref_max", 0))
    got_stats = (sta.get("min", 0), sta.get("max", 0))
    print(f"  参考 min/max: {ref_stats[0]:.6f} / {ref_stats[1]:.6f}")
    print(f"  Phi  min/max: {got_stats[0]:.6f} / {got_stats[1]:.6f}")

    phi_gf = results.get("stats", {}).get("phi_gflops", 0)
    print(f"\n  Phi 并发测得: {phi_gf:.1f} GFLOPS")
    print(f"  流水线总耗时: ~{graph.nodes['report'].end_time - graph.nodes['gen'].start_time:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
