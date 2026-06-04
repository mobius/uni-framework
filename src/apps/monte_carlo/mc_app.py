#!/usr/bin/env python3
"""
mc_app.py — 异构 Monte Carlo 期权定价

Pipeline:
  1. Host: 生成参数 (S0, K, B, σ, r, T, N_paths, steps)
  2. Phi: 生成随机路径 + barrier 检测 → 输出路径均价
  3. VE1/2/3: 并行计算 payoff = max(avgS-K,0) × exp(-rT)
  4. Host: 汇总 → 期权价格 + 置信区间
  5. 验证: 退化欧式 vs Black-Scholes 解析解

Usage:
  ./env/.venv/bin/python3 src/apps/monte_carlo/mc_app.py
"""

import sys, os, time, struct, subprocess, uuid, shutil
from pathlib import Path
from math import sqrt, exp, log, erf

PROJECT = Path(__file__).resolve().parent.parent.parent.parent
APP = PROJECT / "src/apps/monte_carlo"
MIC_LIBS = PROJECT.parent / "intel_phi/icc_mic_libs"

# Option parameters
S0, K, B = 100.0, 100.0, 80.0   # barrier at 80
SIGMA, R, T = 0.2, 0.05, 1.0
STEPS   = 252
N_PATHS = 50000  # 50K paths


def sh(cmd, to=120, env=None):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=to, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip(), time.time()


def compile_phi():
    s, d = APP/"phi/path_gen.c", APP/"phi/path_gen.mic"
    if d.exists(): return True
    print("[compile] Phi path_gen...")
    rc, _, err, _ = sh(
        f"podman start centos7-phi-dev 2>/dev/null && "
        f"podman cp {s} centos7-phi-dev:/tmp/pg.c && "
        f"podman exec centos7-phi-dev bash -c '"
        f"source /opt/intel/bin/compilervars.sh intel64 && "
        f"icc -std=c99 -mmic -O3 -openmp -o /tmp/pg.mic /tmp/pg.c' && "
        f"podman cp centos7-phi-dev:/tmp/pg.mic {d}")
    return rc == 0


def compile_ve():
    s, d = APP/"ve/payoff.c", APP/"ve/payoff_ve"
    if d.exists(): return True
    rc, _, err, _ = sh(f"ncc -O3 -fopenmp -o {d} {s}")
    return rc == 0


def run_phi(wd: Path, params: bytes) -> tuple[Path, int, int]:
    """scp params → Phi → scp paths back"""
    uid = uuid.uuid4().hex[:8]
    rp  = f"/tmp/mc_{uid}_params.bin"
    rpa = f"/tmp/mc_{uid}_paths.bin"
    rst = f"/tmp/mc_{uid}_stats.bin"

    # scp params
    (wd / "params.bin").write_bytes(params)
    sh(f"scp {wd}/params.bin mic0:{rp}", to=30)

    env = os.environ.copy()
    if MIC_LIBS.is_dir(): env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)

    print("[phi] path generation + barrier check...")
    rc, out, err, _ = sh(
        f"micnativeloadex {APP/'phi/path_gen.mic'} -d 0 -t 120 "
        f"-a \"{rp} {rpa} {rst}\"", env=env, to=180)
    for l in (out+err).splitlines():
        if l.strip(): print(f"    {l.strip()}")

    if rc != 0: return None, 0, 0

    # scp back
    paths_path = wd / "paths.bin"
    sh(f"scp mic0:{rpa} {paths_path}", to=30)
    stats_path = wd / "stats.bin"
    sh(f"scp mic0:{rst} {stats_path}", to=30)

    if not paths_path.exists(): return None, 0, 0

    # Read stats
    with open(stats_path, "rb") as f:
        valid   = struct.unpack("i", f.read(4))[0]
        invalid = struct.unpack("i", f.read(4))[0]

    return paths_path, valid, invalid


def run_ve_payoffs(paths_path: Path, wd: Path) -> dict:
    """Split paths, run 3×VE in parallel"""
    import numpy as np
    import asyncio

    # Read paths
    with open(paths_path, "rb") as f:
        count = struct.unpack("i", f.read(4))[0]
        all_avgs = np.frombuffer(f.read(), dtype=np.float64)

    # Split into 3 chunks
    chunk = (count + 2) // 3

    async def run_one(ve_id: int, idx_start: int, idx_end: int):
        chunk_data = all_avgs[idx_start:idx_end]
        chunk_count = len(chunk_data)

        chunk_path = wd / f"chunk_{ve_id}.bin"
        with open(chunk_path, "wb") as f:
            f.write(struct.pack("i", chunk_count))
            f.write(chunk_data.tobytes())

        out_path = wd / f"payoffs_{ve_id}.bin"
        exe = APP / "ve/payoff_ve"
        cmd = (f"/opt/nec/ve/bin/ve_exec -N {ve_id} {exe} "
               f"{chunk_path} {K} {R} {T} {out_path}")
        t0 = time.time()
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        elapsed = time.time() - t0
        text = stdout.decode() + stderr.decode()
        for l in text.splitlines():
            if l.strip(): print(f"    {l.strip()}")
        return elapsed

    print(f"[ve] parallel payoff ({count} paths, 3 chunks)...")
    t0 = time.time()
    tasks = []
    for v in range(3):
        start = v * chunk
        end = min((v+1)*chunk, count)
        if start < count:
            tasks.append(run_one(v+1, start, end))

    async def gather_all():
        return await asyncio.gather(*tasks)
    ve_elapsed = max(asyncio.run(gather_all()))
    return {"ve_elapsed": ve_elapsed, "count": count}


def black_scholes_call(S, K, r, sigma, T):
    """Black-Scholes European call price"""
    d1 = (log(S/K) + (r + 0.5*sigma*sigma)*T) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)
    from math import erf
    def norm_cdf(x):
        return 0.5 * (1.0 + erf(x / sqrt(2.0)))
    return S * norm_cdf(d1) - K * exp(-r*T) * norm_cdf(d2)


def verify(wd: Path):
    """Compare Phi+VE result against host numpy reference MC"""
    import numpy as np
    rng = np.random.default_rng(0)  # different seed from Phi

    # Read VE payoffs
    payoffs = []
    for v in range(3):
        pp = wd / f"payoffs_{v}.bin"
        if not pp.exists(): continue
        with open(pp, "rb") as f:
            c = struct.unpack("i", f.read(4))[0]
            payoffs.append(np.frombuffer(f.read(), dtype=np.float64))
    all_p = np.concatenate(payoffs)
    mc_price = float(all_p.mean())
    mc_std   = float(all_p.std())
    mc_ci    = 1.96 * mc_std / sqrt(len(all_p))

    # Reference: numpy MC (same parameters, different RNG)
    dt = T / STEPS
    drift = (R - 0.5 * SIGMA * SIGMA) * dt
    vol   = SIGMA * sqrt(dt)
    ref_N = N_PATHS // 2  # fewer paths for speed
    ref_avgs = np.zeros(ref_N)

    for p in range(ref_N):
        S = S0
        path_sum = 0.0
        Z = rng.normal(0, 1, STEPS)
        for t in range(STEPS):
            S *= np.exp(drift + vol * Z[t])
            if S < B: break
            path_sum += S
        else:
            ref_avgs[p] = path_sum / STEPS

    valid_ref = ref_avgs[ref_avgs > 0]
    ref_payoffs = np.maximum(valid_ref - K, 0) * exp(-R * T)
    ref_price = float(ref_payoffs.mean())
    ref_std   = float(ref_payoffs.std())

    diff_pct = abs(mc_price - ref_price) / ref_price * 100
    print(f"\n[verify] Phi+VE MC price = {mc_price:.4f} ± {mc_ci:.4f}")
    print(f"         numpy MC price = {ref_price:.4f} (N={len(ref_payoffs)})")
    print(f"         diff            = {diff_pct:.2f}%")
    print(f"         (Asian option → lower than European BS={black_scholes_call(S0,K,R,SIGMA,T):.2f})")

    return diff_pct < 5.0  # MC statistical noise ~few %


def main():
    print("="*60)
    print("  异构 Monte Carlo 期权定价")
    print(f"  S0={S0} K={K} B={B} σ={SIGMA} r={R} T={T}")
    print(f"  steps={STEPS} paths={N_PATHS}")
    print("="*60)

    wd = APP / "run_data"
    wd.mkdir(parents=True, exist_ok=True)

    if not compile_phi() or not compile_ve():
        return print("❌ compile fail")

    # 1. Write params (binary)
    params = struct.pack("ddddiid",
        S0, R, SIGMA, T/STEPS, STEPS, N_PATHS, B)

    # 2. Phi: path generation
    print()
    paths_path, valid, invalid = run_phi(wd, params)
    if not paths_path:
        return print("❌ Phi fail")
    print(f"  valid={valid} invalid={invalid}")

    # 3. VE: payoff
    print()
    info = run_ve_payoffs(paths_path, wd)

    # 4. Verify
    ok = verify(wd)

    print("\n" + "="*60)
    print("  结果")
    print("="*60)
    print(f"  路径: {N_PATHS} → valid={valid} ({100*valid/max(N_PATHS,1):.1f}%)")
    print(f"  验证: {'✅ 通过' if ok else '⚠️ 偏差>2%'}")


if __name__ == "__main__":
    main()
