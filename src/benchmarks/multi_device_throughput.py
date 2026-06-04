"""
multi_device_throughput.py — TC-HETERO-002 封装
数据中心吞吐: 4 卡并行 DGEMM/FMA 总算力

Usage:
    from benchmarks.multi_device_throughput import run
    result = run()  # → {"total_tflops": 5.68, "passed": True}
"""

import subprocess, sys, time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent


def run(n: int = 2048) -> dict:
    """Run multi-device throughput test.

    Returns:
        dict with keys: total_gflops, total_tflops, ve_gflops, phi_gflops, elapsed, passed
    """
    script = PROJECT / "scripts" / "bench_throughput.py"
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=120,
        cwd=str(PROJECT))
    elapsed = time.time() - t0

    result = {
        "elapsed": elapsed,
        "total_gflops": 0.0,
        "total_tflops": 0.0,
        "passed": False,
        "raw_output": r.stdout[-2000:] if r.stdout else "",
    }

    for line in r.stdout.splitlines():
        if "总算力" in line and "TFLOPS" in line:
            try:
                parts = line.split()
                for i, p in enumerate(parts):
                    if "TFLOPS" in p:
                        result["total_tflops"] = float(parts[i-1])
                        result["total_gflops"] = result["total_tflops"] * 1000
            except (ValueError, IndexError):
                pass
        if "✅ 通过" in line:
            result["passed"] = True

    return result


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
