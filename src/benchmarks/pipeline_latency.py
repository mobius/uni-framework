"""
pipeline_latency.py — TC-HETERO-003 封装
流水线延迟: 纯 VE 链 vs 含 Phi 链

Usage:
    from benchmarks.pipeline_latency import run
    result = run()  # → {"ve_only": 0.41, "with_phi": 2.75, "overhead_pct": 569}
"""

import subprocess, sys, time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent


def run() -> dict:
    """Run pipeline latency comparison.

    Returns:
        dict with keys: ve_only_s, with_phi_s, overhead_pct, passed
    """
    script = PROJECT / "scripts" / "bench_pipeline_latency.py"
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=120,
        cwd=str(PROJECT))
    elapsed = time.time() - t0

    result = {
        "elapsed": elapsed,
        "ve_only_s": 0.0,
        "with_phi_s": 0.0,
        "overhead_pct": 0.0,
        "passed": False,
        "raw_output": r.stdout[-2000:] if r.stdout else "",
    }

    for line in r.stdout.splitlines():
        if "纯 VE 链" in line:
            try:
                result["ve_only_s"] = float(line.split(":")[-1].strip().rstrip("s"))
            except ValueError:
                pass
        if "含 Phi 链" in line:
            try:
                result["with_phi_s"] = float(line.split(":")[-1].strip().rstrip("s"))
            except ValueError:
                pass
        if "Overhead" in line:
            try:
                result["overhead_pct"] = float(line.split(":")[-1].strip().rstrip("%"))
            except ValueError:
                pass
        if "✅ 通过" in line:
            result["passed"] = True

    return result


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
