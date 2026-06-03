#!/usr/bin/env python3.11
"""
multi_task/task_flow.py — 多卡异构任务流演示

DAG:
  task_gen (Host) ──┬── task_matmul_1 (VE1) ──┐
                    ├── task_matmul_2 (VE2) ──┤
                    ├── task_matmul_3 (VE3) ──┼── task_agg (VE1) ── task_stats (Host)
                    └── task_phi (Phi) ───────┘

用法:
    python3.11 examples/multi_task/task_flow.py
"""

import sys
import os
import subprocess
import asyncio
import time
import struct
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT / "src"))

from scheduler.task_graph import TaskGraph, TaskNode

# Paths
WORKDIR   = PROJECT / "examples" / "multi_task" / "run_data"
KERNEL_VE = PROJECT / "src" / "kernels" / "ve"
KERNEL_PH = PROJECT / "src" / "kernels" / "phi"
MIC_LIBS  = PROJECT.parent / "intel_phi" / "icc_mic_libs"

N = 512  # matrix size: 512×512 (2MB per matrix, fast demo)

# ─── Utilities ───────────────────────────────────────────────

def run(cmd: str, timeout: int = 120, env: dict = None) -> dict:
    """Run a shell command, return {'status','stdout','stderr','elapsed'}"""
    t0 = time.time()
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                            timeout=timeout, env=env or os.environ)
    elapsed = time.time() - t0
    return {
        "status": "pass" if result.returncode == 0 else "fail",
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "elapsed": elapsed,
    }


def compile_ve(name: str, src: str) -> bool:
    """Compile a VE kernel"""
    exe = KERNEL_VE / name
    if exe.exists():
        return True
    cmd = f"ncc -O3 -fopenmp -o {exe} {KERNEL_VE / src}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    ok = result.returncode == 0
    if ok:
        print(f"  [compile] {name} ✅")
    else:
        print(f"  [compile] {name} ❌\n{result.stderr}")
    return ok


def compile_phi(name: str, src: str) -> bool:
    """Compile a Phi kernel via podman"""
    exe = KERNEL_PH / name
    if exe.exists():
        return True
    
    os.system("podman start centos7-phi-dev 2>/dev/null")
    
    cmd = (
        f"podman cp {KERNEL_PH / src} centos7-phi-dev:/tmp/{src} && "
        f"podman exec centos7-phi-dev bash -c '"
        f"source /opt/intel/bin/compilervars.sh intel64 && "
        f"icc -std=c99 -mmic -O3 -openmp -static-intel -o /tmp/{name} /tmp/{src}' && "
        f"podman cp centos7-phi-dev:/tmp/{name} {exe}"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    ok = result.returncode == 0
    if ok:
        print(f"  [compile] {name} ✅")
    else:
        print(f"  [compile] {name} ❌\n{result.stderr}")
    return ok


def write_matrix(path: Path, A, B):
    """Write binary: [int32 N][double A[N*N]][double B[N*N]]"""
    data = struct.pack("i", N)
    data += A.tobytes()
    data += B.tobytes()
    path.write_bytes(data)


def read_matrix(path: Path):
    """Read binary: [int32 N][double M[N*N]] → (N, numpy_array)"""
    import numpy as np
    with open(path, "rb") as f:
        N_read = struct.unpack("i", f.read(4))[0]
        arr = np.frombuffer(f.read(), dtype=np.float64)
    return N_read, arr.reshape(N_read, N_read)


# ─── Task Functions ──────────────────────────────────────────

def task_gen():
    """Task 0: Generate data on Host (Python numpy)
    
    Generates 3 independent matrix pairs A_i, B_i (each N×N).
    Each VE gets one pair. Reference: host computes sum(A_i × B_i).
    """
    import numpy as np
    
    os.makedirs(WORKDIR, exist_ok=True)
    
    rng = np.random.default_rng(42)
    C_ref_sum = np.zeros((N, N), dtype=np.float64)
    
    for idx in range(3):
        A = rng.normal(0, 0.01, (N, N)).astype(np.float64)
        B = rng.normal(0, 0.01, (N, N)).astype(np.float64)
        write_matrix(WORKDIR / f"input_{idx + 1}.bin", A, B)
        C_ref_sum += A @ B
    
    return {
        "status": "pass",
        "N": N,
        "checksum_ref": float(np.sum(C_ref_sum)),
        "files": [str(WORKDIR / f"input_{i}.bin") for i in range(1, 4)],
    }


def task_matmul(ve_id: int, block_id: int, depends_on: list):
    """Task 1/2/3: VE matrix multiplication on a block"""
    input_file  = WORKDIR / f"input_{block_id}.bin"
    output_file = WORKDIR / f"result_{block_id}.bin"
    exe = KERNEL_VE / "matmul_block_ve"
    
    cmd = f"/opt/nec/ve/bin/ve_exec -N {ve_id} {exe} {input_file} {output_file}"
    result = run(cmd, timeout=120)
    
    # Parse GFLOPS from output
    gflops = 0.0
    for line in result["stdout"].splitlines():
        if "GFLOPS" in line:
            try:
                gflops = float(line.split("GFLOPS")[0].split()[-1])
            except (ValueError, IndexError):
                pass
    
    result["gflops"] = gflops
    return result


def task_aggregate():
    """Task 4: Aggregate 3 block results on VE1"""
    output_file = WORKDIR / "final_result.bin"
    exe = KERNEL_VE / "aggregate_ve"
    
    cmd = (
        f"/opt/nec/ve/bin/ve_exec -N 1 {exe} "
        f"{WORKDIR}/result_1.bin {WORKDIR}/result_2.bin "
        f"{WORKDIR}/result_3.bin {output_file}"
    )
    return run(cmd, timeout=60)


def task_phi_peak():
    """Task phi: FP64 peak test on Phi (并行于 VE matmul)
    
    无文件 I/O — 直接跑 micnativeloadex, 纯 stdout 输出 GFLOPS.
    依赖 gen 只是为了串行化启动, 不消费 gen 的数据.
    """
    exe = KERNEL_PH / "peak_fp64.mic"
    
    # 复用 Basic 示例的峰值内核
    if not exe.exists():
        print("[phi] peak_fp64.mic 不存在, 尝试编译...")
        os.system("podman start centos7-phi-dev 2>/dev/null")
        # 复制源码到容器编译
        src = KERNEL_PH / "peak_fp64.c"
        cmd = (
            f"podman cp {src} centos7-phi-dev:/tmp/peak_fp64.c && "
            f"podman exec centos7-phi-dev bash -c '"
            f"source /opt/intel/bin/compilervars.sh intel64 && "
            f"icc -std=c99 -mmic -O3 -openmp -o /tmp/peak_fp64.mic /tmp/peak_fp64.c' && "
            f"podman cp centos7-phi-dev:/tmp/peak_fp64.mic {exe}"
        )
        subprocess.run(cmd, shell=True, capture_output=True, timeout=120)
    
    env = os.environ.copy()
    if MIC_LIBS.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)
    
    cmd = f"micnativeloadex {exe} -d 0 -t 60"
    result = run(cmd, timeout=120, env=env)
    
    # Parse GFLOPS from Phi output
    gflops = 0.0
    for line in result["stdout"].splitlines():
        if "GFLOPS" in line:
            try:
                gflops = float(line.split(":")[-1].strip().split()[0])
            except (ValueError, IndexError):
                pass
    
    result["gflops"] = gflops
    return result


def task_stats():
    """Task 5: Compute statistics on Host (Python)
    
    Reads final_result.bin, computes min/max/mean/stddev.
    Demonstrates Host participating in the task DAG alongside accelerators.
    """
    import numpy as np
    import struct
    
    input_file = WORKDIR / "final_result.bin"
    output_file = WORKDIR / "stats_report.txt"
    
    with open(input_file, "rb") as f:
        N = struct.unpack("i", f.read(4))[0]
        arr = np.frombuffer(f.read(), dtype=np.float64).reshape(N, N)
    
    stats = {
        "N": N,
        "elements": N * N,
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "stddev": float(arr.std()),
    }
    
    with open(output_file, "w") as f:
        f.write(f"N={N} elements={N*N}\n")
        f.write(f"min={stats['min']:.6f} max={stats['max']:.6f}\n")
        f.write(f"mean={stats['mean']:.6f} stddev={stats['stddev']:.6f}\n")
    
    return {"status": "pass", "stats": stats, "stdout": str(stats)}


# ─── Main ────────────────────────────────────────────────────

async def main():
    banner = lambda s: print(f"\n{'='*60}\n  {s}\n{'='*60}")
    
    # Step 1: Compile
    banner("编译内核")
    ok = all([
        compile_ve("matmul_block_ve", "matmul_block.c"),
        compile_ve("aggregate_ve", "aggregate.c"),
    ])
    if not ok:
        print("❌ 编译失败，退出")
        return
    
    # Step 2: Clean workdir
    os.makedirs(WORKDIR, exist_ok=True)
    for f in WORKDIR.glob("*.bin"):
        f.unlink()
    for f in WORKDIR.glob("*.txt"):
        f.unlink()
    
    # Step 3: Build DAG
    banner("构建任务 DAG")
    
    graph = TaskGraph()
    
    graph.add(TaskNode("gen",       "host", task_gen))
    graph.add(TaskNode("matmul_1",  "ve1",  lambda: task_matmul(1, 1, ["gen"]),    depends_on=["gen"]))
    graph.add(TaskNode("matmul_2",  "ve2",  lambda: task_matmul(2, 2, ["gen"]),    depends_on=["gen"]))
    graph.add(TaskNode("matmul_3",  "ve3",  lambda: task_matmul(3, 3, ["gen"]),    depends_on=["gen"]))
    graph.add(TaskNode("phi_peak",  "phi0", task_phi_peak,                          depends_on=["gen"]))
    graph.add(TaskNode("aggregate", "ve1",  task_aggregate,                         depends_on=["matmul_1", "matmul_2", "matmul_3"]))
    graph.add(TaskNode("stats",     "host", task_stats,                             depends_on=["aggregate"]))
    
    # Step 4: Execute
    banner("执行任务流")
    results = await graph.execute(verbose=True)
    
    # Step 5: Verification
    banner("结果验证")
    
    gen = results.get("gen", {})
    ref_checksum = gen.get("checksum_ref", 0)
    
    print(f"\n  参考校验和 (Host numpy): {ref_checksum:.1f}")
    
    # Read final result
    if (WORKDIR / "final_result.bin").exists():
        _, final_mat = read_matrix(WORKDIR / "final_result.bin")
        final_sum = float(final_mat.sum())
        print(f"  最终结果校验和:          {final_sum:.1f}")
        
        diff = abs(final_sum - ref_checksum) / max(abs(ref_checksum), 1.0)
        if diff < 1e-6:
            print(f"  ✅ 校验和匹配 (差异 {diff:.2e})")
        else:
            print(f"  ⚠️  校验和不匹配 (差异 {diff:.2e})")
    
    # Show stats report
    report_file = WORKDIR / "stats_report.txt"
    if report_file.exists():
        print(f"\n  Phi 统计报告:")
        for line in report_file.read_text().strip().splitlines():
            print(f"    {line}")
    
    # Summary
    banner("任务流汇总")
    total_gflops = 0.0
    for name, r in results.items():
        gflops = r.get("gflops", 0.0)
        if gflops:
            total_gflops += gflops
            print(f"  {name}: {gflops:.1f} GFLOPS")
    
    print(f"\n  总算力: {total_gflops:.1f} GFLOPS")
    
    all_pass = all(r.get("status") == "pass" for r in results.values())
    if all_pass:
        print("  🎉 全部任务通过")
    else:
        print("  ⚠️  部分任务失败")


if __name__ == "__main__":
    asyncio.run(main())
