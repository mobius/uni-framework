#!/usr/bin/env python3
"""
bench_power.py — TC-HETERO-005/006: 功率封顶 + 稳定性验证

TC-005: 逐步加载 → 实测功耗 vs PowerCap 预估
TC-006: 5 分钟混合负载 → 温度/功耗趋势

通过标准:
  TC-005: 实测峰值 ≤ 1600W, PowerCap 超额排队
  TC-006: 5min 无失败, 温度稳定 (Δ ≤ 20°C)

Usage:
  ./env/.venv/bin/python3 scripts/bench_power.py
"""

import sys, os, time, subprocess, json, re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "src"))

# VE loads
VE_PEAK = PROJECT / "src/kernels/ve/peak_fp64_ve"
VE_DGEMM = PROJECT / "src/kernels/ve/dgemm_nlc_ve"
PHI_PEAK = PROJECT / "src/kernels/phi/peak_fp64.mic"
THRU_DATA = PROJECT / "examples/throughput/run_data"
MIC_LIBS = PROJECT.parent / "intel_phi/icc_mic_libs"


def sh(cmd, to=120, env=None):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=to, env=env)


@dataclass
class VEPower:
    """veda-smi 解析结果"""
    card: int
    temp_c: float = 0.0
    power_w: float = 0.0
    clock_mhz: float = 0.0
    mem_mhz: float = 0.0

    @classmethod
    def read_all(cls) -> list["VEPower"]:
        """Parse veda-smi output for all VE cards"""
        r = sh("/opt/nec/ve/bin/veda-smi", to=10)
        text = r.stdout + r.stderr
        cards = []
        current = None

        for line in text.splitlines():
            m = re.match(r"┌── #(\d)", line)
            if m:
                current = VEPower(card=int(m.group(1)))
                cards.append(current)
            if current and "Temp:" in line:
                temps = [float(t.rstrip("°C")) for t in re.findall(r"(\d+\.\d+)°C", line)]
                if temps:
                    current.temp_c = max(temps)
            if current and "Power:" in line:
                m = re.search(r"(\d+\.\d+)W", line)
                if m:
                    current.power_w = float(m.group(1))
            if current and "Clock:" in line:
                m = re.search(r"current:\s*(\d+)\s*MHz", line)
                if m:
                    current.clock_mhz = float(m.group(1))
                m = re.search(r"memory:\s*(\d+)\s*MHz", line)
                if m:
                    current.mem_mhz = float(m.group(1))
        return cards


def read_rapl_w() -> float:
    """Read Intel RAPL package power (watts)"""
    import glob
    try:
        total = 0.0
        for entry in sorted(glob.glob("/sys/class/powercap/intel-rapl:*")):
            name_file = f"{entry}/name"
            energy_file = f"{entry}/energy_uj"
            try:
                with open(name_file) as f:
                    if "package" not in f.read().strip():
                        continue
                with open(energy_file) as f:
                    total += float(f.read().strip())
            except (IOError, ValueError):
                continue
        return total / 1e6  # uJ → J (cumulative, not instantaneous)
    except Exception:
        return -1


def run_ve_load(ve_id: int, duration_s: int = 10) -> subprocess.Popen:
    """Start a VE FMA load, return process handle"""
    cmd = f"/opt/nec/ve/bin/ve_exec -N {ve_id} {VE_PEAK}"
    return subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def run_phi_load() -> subprocess.Popen:
    """Start a Phi FMA load"""
    env = os.environ.copy()
    if MIC_LIBS.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)
    cmd = f"micnativeloadex {PHI_PEAK} -d 0 -t 60"
    return subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=env)


def run_ve_dgemm(ve_id: int) -> subprocess.Popen:
    """Start a VE NLC DGEMM load"""
    inp = THRU_DATA / f"mat_{ve_id}.bin"
    out = THRU_DATA / f"result_pwr_{ve_id}.bin"
    nlc_env = os.environ.copy()
    nlc_env["VE_LD_LIBRARY_PATH"] = "/opt/nec/ve/nlc/3.1.0/lib"
    cmd = f"/opt/nec/ve/bin/ve_exec -N {ve_id} {VE_DGEMM} {inp} {out}"
    return subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=nlc_env)


def sample_power() -> dict:
    """采集当前功耗快照"""
    ve = VEPower.read_all()
    rapl = read_rapl_w()
    ve_total = sum(c.power_w for c in ve)
    ve_temps = [c.temp_c for c in ve]
    return {
        "ve_w": [c.power_w for c in ve],
        "ve_total_w": ve_total,
        "ve_temp_max": max(ve_temps) if ve_temps else 0,
        "rapl_j": rapl,
        "timestamp": time.time(),
    }


def step_load(desc: str, procs) -> dict:
    """加载设备并采样功耗"""
    print(f"\n  [{desc}]")
    time.sleep(5)
    s = sample_power()
    print(f"    VE power: {[f'{w:.0f}W' for w in s['ve_w']]}")
    print(f"    VE total:  {s['ve_total_w']:.0f}W")
    print(f"    TEMP max:  {s['ve_temp_max']:.1f}°C")
    return s


def main():
    print("=" * 60)
    print("  TC-HETERO-005/006: 功率封顶 + 5min 稳定性")
    print("=" * 60)

    # ═══════════ TC-005: 逐步加载 ═══════
    print("\n── TC-005: 逐步加载功耗实测 ──")

    # Step 0: baseline
    print("\n  [Step 0: idle]")
    time.sleep(2)
    baseline = sample_power()
    print(f"    VE power: {[f'{w:.0f}W' for w in baseline['ve_w']]}")
    print(f"    VE total:  {baseline['ve_total_w']:.0f}W")
    print(f"    TEMP max:  {baseline['ve_temp_max']:.1f}°C")

    results = [("idle", baseline)]

    # Step 1: VE1 only
    p1 = run_ve_load(1, 30)
    s = step_load("VE1 load", [p1])
    results.append(("+VE1", s))
    p1.terminate(); p1.wait()

    # Step 2: VE1+VE2
    p1 = run_ve_load(1, 30); p2 = run_ve_load(2, 30)
    s = step_load("VE1+VE2 load", [p1, p2])
    results.append(("+VE2", s))
    p1.terminate(); p2.terminate(); p1.wait(); p2.wait()

    # Step 3: VE1+VE2+VE3
    p1 = run_ve_load(1, 60); p2 = run_ve_load(2, 60); p3 = run_ve_load(3, 60)
    s = step_load("VE1+VE2+VE3 load", [p1, p2, p3])
    results.append(("+VE3", s))
    p1.terminate(); p2.terminate(); p3.terminate()
    p1.wait(); p2.wait(); p3.wait()

    # Step 4: all + Phi
    p1 = run_ve_load(1, 60); p2 = run_ve_load(2, 60); p3 = run_ve_load(3, 60)
    pphi = run_phi_load()
    s = step_load("VE+Phi full load", [p1, p2, p3, pphi])
    results.append(("+Phi", s))
    p1.terminate(); p2.terminate(); p3.terminate(); pphi.wait()
    p1.wait(); p2.wait(); p3.wait()

    # PowerCap check
    from scheduler.power import PowerCap
    cap = PowerCap(psu_limit_w=1600)
    est_total = cap.estimate_batch(
        ["ve1", "ve2", "ve3", "phi0"],
        {"ve1": "fma_peak", "ve2": "fma_peak", "ve3": "fma_peak", "phi0": "fma_peak"})
    peak_ve = max(r[1]["ve_total_w"] for r in results)
    tc005_pass = peak_ve < 1600

    print(f"\n  TC-005 汇总:")
    print(f"    VE 峰值功耗:   {peak_ve:.0f}W")
    print(f"    PowerCap 预估: {est_total:.0f}W (含 CPU = {est_total+300:.0f}W)")
    print(f"    通过 (≤1600W): {'✅' if tc005_pass else '⚠️ 超标'}")

    # ═══════════ TC-006: 5min 稳定性 ═══════
    print(f"\n── TC-006: 5 分钟稳定性 ──")
    DURATION = 300  # 5 minutes
    INTERVAL = 30   # sample every 30s

    # Ensure throughput data exists
    if not (THRU_DATA / "mat_1.bin").exists():
        print("  需先生成测试矩阵...")
        print("  → 运行 scripts/bench_throughput.py")
        sh(f"{sys.executable} {PROJECT}/scripts/bench_throughput.py", to=120)

    # Run continuous mixed load
    print(f"  启动混合负载 (VE NLC DGEMM + Phi FMA), {DURATION//60}min...")
    samples = []
    failures = 0
    t_start = time.time()

    def launch_load():
        ps = []
        for vid in [1, 2, 3]:
            ps.append(run_ve_dgemm(vid))
        ps.append(run_phi_load())
        return ps

    procs = launch_load()
    next_sample = t_start + INTERVAL

    while time.time() - t_start < DURATION:
        time.sleep(2)

        # Check if any process died
        for i, p in enumerate(procs):
            if p.poll() is not None and p.returncode != 0:
                failures += 1
                print(f"    ❌ process {i} failed, restarting...")

        # Restart completed processes
        alive = [p for p in procs if p.poll() is None]
        if len(alive) < 4:
            for p in procs:
                if p.poll() is not None:
                    try: p.wait()
                    except: pass
            # Kill remaining and restart
            for p in alive:
                p.terminate()
            for p in alive:
                try: p.wait(timeout=5)
                except: pass
            procs = launch_load()

        # Sample
        if time.time() >= next_sample:
            s = sample_power()
            elapsed = time.time() - t_start
            samples.append({**s, "elapsed": elapsed, "failures": failures})
            print(f"    [{elapsed:3.0f}s] VE:{s['ve_total_w']:.0f}W  "
                  f"temp:{s['ve_temp_max']:.0f}°C  fail:{failures}")
            next_sample += INTERVAL

    # Cleanup
    for p in procs:
        p.terminate()
    for p in procs:
        try: p.wait(timeout=5)
        except: pass

    # Analysis
    temps = [s["ve_temp_max"] for s in samples if s["ve_temp_max"] > 0]
    temp_rise = max(temps) - baseline["ve_temp_max"] if temps else 0
    tc006_pass = failures == 0 and temp_rise <= 20

    print(f"\n  TC-006 汇总:")
    print(f"    持续:     {DURATION//60}min")
    print(f"    采样点:   {len(samples)}")
    print(f"    失败数:   {failures}")
    print(f"    基线温度: {baseline['ve_temp_max']:.1f}°C")
    print(f"    峰值温度: {max(temps) if temps else 0:.1f}°C")
    print(f"    温升:     {temp_rise:.1f}°C")
    print(f"    通过:     {'✅' if tc006_pass else '❌'}")

    # Final summary
    print("\n" + "=" * 60)
    print("  Phase 3 收尾")
    print("=" * 60)
    print(f"  TC-005 功率封顶: {'✅ ≤1600W' if tc005_pass else '⚠️'}")
    print(f"  TC-006 稳定性:   {'✅ 无失败,ΔT≤20°C' if tc006_pass else '❌'}")



if __name__ == "__main__":
    main()
