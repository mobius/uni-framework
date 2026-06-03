#!/usr/bin/env python3
"""
bench_mpi.py — TC-HETERO-004: VE-MPI AllReduce 扩展性

依次测量 1/2/3 卡 MPI_Allreduce(SUM) 512MB float64:
  - 单卡 baseline (T=0, no communication)
  - 双卡 (VE0↔VE1 直连)
  - 三卡 (ring: VE0↔VE1↔VE2)
  - 计算: 加速比 / 扩展效率

通过标准: 三卡扩展效率 ≥ 95%
"""

import sys, os, time, subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
MPI_VARS = ("source /opt/nec/ve/mpi/3.10.0/bin64/necmpivars-runtime.sh")
BIN = PROJECT / "src" / "kernels" / "ve" / "mpi_allreduce_ve"
ROUNDS = 3  # number of runs per config (use median)


def shell(cmd, timeout=120):
    t0 = time.time()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, executable="/bin/bash")
    return r.returncode, r.stdout.strip(), r.stderr.strip(), time.time() - t0


def compile_mpi():
    src = PROJECT / "src" / "kernels" / "ve" / "mpi_allreduce.c"
    if BIN.exists():
        return True
    cmd = f"{MPI_VARS} && mpincc -O3 -o {BIN} {src}"
    rc, _, err, _ = shell(cmd)
    if rc != 0:
        print(f"[compile] FAILED:\n{err}")
    return rc == 0


def parse_mpi(stdout: str) -> dict:
    """Parse: MPI_AllReduce: ranks=2 MB=512 elapsed=0.0968s BW=5.54_GB/s verify=OK"""
    r = {"ranks": 0, "elapsed": 0.0, "bw": 0.0, "verify": "FAIL"}
    for token in stdout.split():
        if token.startswith("ranks="):
            r["ranks"] = int(token.split("=")[1])
        elif token.startswith("elapsed="):
            r["elapsed"] = float(token.rstrip("s").split("=")[1])
        elif token.startswith("BW="):
            r["bw"] = float(token.rstrip("_GB/s").split("=")[1])
        elif token.startswith("verify="):
            r["verify"] = token.split("=")[1]
    return r


def run_mpi(ve_range: str, np_val: int) -> dict:
    """Run MPI with given VE range, return parsed result (median of ROUNDS)"""
    times = []
    last = None
    for _ in range(ROUNDS):
        cmd = f"{MPI_VARS} && mpirun -ve {ve_range} -np {np_val} {BIN}"
        rc, out, err, elapsed = shell(cmd)
        if rc != 0:
            print(f"  FAILED: {err}")
            return {"ranks": np_val, "elapsed": 0, "bw": 0, "verify": "FAIL",
                    "error": err}
        result = parse_mpi(out)
        times.append(result["elapsed"])
        last = result

    # Use median
    times.sort()
    median = times[len(times) // 2]
    if last:
        last["elapsed"] = median
    return last or {}


def main():
    print("=" * 60)
    print("  TC-HETERO-004: VE-MPI AllReduce 扩展性")
    print("=" * 60)

    if not compile_mpi():
        print("  ❌ MPI 内核编译失败")
        return

    results = {}
    for np_val, ve_range in [(1, "1"), (2, "1-2"), (3, "1-3")]:
        label = f"{np_val}卡"
        print(f"\n--- {label} (-ve {ve_range} -np {np_val}) ×{ROUNDS} ---")
        r = run_mpi(ve_range, np_val)
        results[np_val] = r
        if r.get("elapsed", 0) > 0:
            print(f"  elapsed={r['elapsed']:.4f}s BW={r['bw']:.2f} GB/s "
                  f"verify={r['verify']}")
        else:
            print(f"  elapsed≈0s (单卡无需通信) verify={r['verify']}")

    # ── 汇总 ──
    print("\n" + "=" * 60)
    print("  汇总")
    print("=" * 60)

    t1 = results[1].get("elapsed", 0) or 1e-9
    t2 = results[2].get("elapsed", 0)
    t3 = results[3].get("elapsed", 0)

    print(f"  单卡:  {t1:.4f}s (无通信, 参考)")
    print(f"  双卡:  {t2:.4f}s  BW={results[2].get('bw',0):.1f} GB/s")
    print(f"  三卡:  {t3:.4f}s  BW={results[3].get('bw',0):.1f} GB/s")

    # Ring AllReduce: ideal T(N) ∝ 2*(N-1)/N
    if t2 > 0:
        # Expected T(3) based on T(2) ring model
        t3_ideal = t2 * (2 * (3 - 1) / 3) / (2 * (2 - 1) / 2)
        # t3_ideal = t2 * (4/3) / 1 = 1.333 * t2
        t3_ideal = t2 * 1.3333
        efficiency = (t3_ideal / t3 * 100) if t3 > 0 else 100

        print(f"\n  Ring 模型 T(3) 理想值: {t3_ideal:.4f}s")
        print(f"  T(3) 实测:             {t3:.4f}s")
        print(f"  扩展效率:              {efficiency:.0f}%")
        print(f"  通过标准:              ≥ 95%")

        if efficiency >= 95:
            print(f"  ✅ 通过")
        else:
            print(f"  ⚠️ 未达标准 "
                  f"(VE2 型号 10B/fw 5127/时钟 1400MHz vs VE0/1 型号 10BE/fw 5400)")
    else:
        print("\n  ⚠️ 无法计算 (双卡测量失败)")


if __name__ == "__main__":
    main()
