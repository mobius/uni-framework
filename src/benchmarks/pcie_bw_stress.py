"""
pcie_bw_stress.py — TC-HETERO-001 封装
PCIe 带宽压力: 4 卡并发 H2D/D2H 传输

Usage:
    from benchmarks.pcie_bw_stress import run
    result = run()  # → {"h2d_total": 13.8, "eff": 0.86, "passed": False}
"""

import subprocess, sys, time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent


def run(mb: int = 256) -> dict:
    """Run PCIe bandwidth stress test.

    Returns:
        dict with keys: h2d_total, d2h_total, combined, solo_ref, efficiency, passed
    """
    script = PROJECT / "scripts" / "bench_pcie.py"
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=120,
        cwd=str(PROJECT))
    elapsed = time.time() - t0

    result = {
        "elapsed": elapsed,
        "h2d_total": 0.0,
        "d2h_total": 0.0,
        "combined": 0.0,
        "efficiency": 0.0,
        "passed": False,
        "raw_output": r.stdout[-2000:] if r.stdout else "",
    }

    for line in r.stdout.splitlines():
        if "H2D 合计" in line:
            try:
                result["h2d_total"] = float(line.split(":")[-1].strip().split()[0])
            except ValueError:
                pass
        if "D2H 合计" in line:
            try:
                result["d2h_total"] = float(line.split(":")[-1].strip().split()[0])
            except ValueError:
                pass
        if "并发总吞吐" in line:
            try:
                result["combined"] = float(line.split(":")[-1].strip().split()[0])
            except ValueError:
                pass
        if "并发效率" in line:
            try:
                result["efficiency"] = float(line.split(":")[-1].strip().rstrip("%")) / 100
            except ValueError:
                pass
        if "✅ 通过" in line:
            result["passed"] = True

    return result


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
