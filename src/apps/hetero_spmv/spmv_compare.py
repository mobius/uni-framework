#!/usr/bin/env python3
"""
spmv_compare.py — 对比 scp vs VirtIO 文件 I/O 路径

scp 路径:  Phi 读写 mic0:/tmp/ (ramfs)
VirtIO 路径: Phi 读写 mic0:/media/vda/ (VirtIO 块设备 → Host 文件)

两次运行使用相同 CSR 数据，对比各阶段耗时。

Usage:
  ./env/.venv/bin/python3 src/apps/hetero_spmv/spmv_compare.py
"""

import sys, os, time, struct, subprocess, uuid, shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent.parent
APP = PROJECT / "src/apps/hetero_spmv"
MIC_LIBS = PROJECT.parent / "intel_phi/icc_mic_libs"
N, DENSITY = 4096, 0.01


def sh(cmd, to=120, env=None):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=to, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip(), time.time()


def generate_csr(wd: Path):
    import numpy as np
    rng = np.random.default_rng(42)
    nnz = max(int(N * N * DENSITY), 1)
    rows = rng.integers(0, N, nnz, dtype=np.int32)
    cols = rng.integers(0, N, nnz, dtype=np.int32)
    vals = rng.normal(0, 1.0, nnz).astype(np.float64)
    order = np.argsort(rows)
    rows, cols, vals = rows[order], cols[order], vals[order]
    row_ptr = np.zeros(N+1, dtype=np.int32)
    np.add.at(row_ptr, rows+1, 1); np.cumsum(row_ptr, out=row_ptr)
    nnz = int(row_ptr[-1])
    rows, cols, vals = rows[:nnz], cols[:nnz], vals[:nnz]
    x = rng.normal(0, 1.0, N).astype(np.float64)
    wd.mkdir(parents=True, exist_ok=True)
    path = wd / "input.csr"
    with open(path, "wb") as f:
        f.write(struct.pack("i", N))
        f.write(struct.pack("i", nnz))
        f.write(row_ptr.tobytes())
        f.write(cols.astype(np.int32).tobytes())
        f.write(vals.tobytes())
        f.write(x.tobytes())
    y_ref = np.zeros(N)
    for j in range(nnz):
        y_ref[rows[j]] += vals[j] * x[cols[j]]
    (wd / "y_ref.bin").write_bytes(struct.pack("i", N) + y_ref.tobytes())
    return path


def run_path(label: str, csr_path: Path, phi_dir: str, wd: Path) -> dict:
    """Run one I/O path and return timing breakdown"""
    uid = uuid.uuid4().hex[:8]
    exe = APP / "phi/csr_partition.mic"
    env = os.environ.copy()
    if MIC_LIBS.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)

    remote = f"{phi_dir}/spmv_{uid}"
    print(f"\n[{label}] phi_dir={phi_dir}")

    # 1. scp input to Phi
    t0 = time.time()
    sh(f"scp {csr_path} mic0:{remote}_in.csr", to=30)
    t_scp_in = time.time() - t0

    # 2. Phi partition
    t0 = time.time()
    args = f'"{remote}_in.csr {remote}"'
    rc, out, err, _ = sh(
        f"micnativeloadex {exe} -d 0 -t 60 -a {args}", env=env, to=120)
    t_phi = time.time() - t0
    for l in (out+err).splitlines():
        if "Phi block" in l or "Phi done" in l:
            print(f"    {l.strip()}")

    # 3. scp blocks back
    t0 = time.time()
    for i in range(3):
        sh(f"scp mic0:{remote}_block_{i}.bin {wd}/block_{i}.bin", to=30)
    sh(f"scp mic0:{remote}_full.csr {wd}/input.csr", to=30)
    t_scp_out = time.time() - t0

    # 4. VE SpMV
    import asyncio
    ve_exe = APP / "ve/blocked_spmv_ve"

    async def run_ve(ve_id):
        cmd = f"/opt/nec/ve/bin/ve_exec -N {ve_id} {ve_exe} {wd}/block_{ve_id-1}.bin {wd}/y_partial_{ve_id-1}.bin"
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()

    t0 = time.time()
    async def gather_all():
        return await asyncio.gather(run_ve(1), run_ve(2), run_ve(3))
    asyncio.run(gather_all())
    t_ve = time.time() - t0

    # 5. Verify
    import numpy as np
    y_ref = np.frombuffer((wd/"y_ref.bin").read_bytes()[4:], 'f8')
    y_merged = np.zeros(N)
    for i in range(3):
        y_merged += np.frombuffer((wd/f"y_partial_{i}.bin").read_bytes()[4:], 'f8')
    max_diff = float(np.abs(y_merged - y_ref).max())

    return {
        "t_scp_in": t_scp_in, "t_phi": t_phi, "t_scp_out": t_scp_out,
        "t_ve": t_ve, "t_total": t_scp_in + t_phi + t_scp_out + t_ve,
        "max_diff": max_diff,
    }


def main():
    print("=" * 60)
    print("  SpMV I/O 路径对比: scp (/tmp) vs VirtIO (/media/vda)")
    print("=" * 60)

    wd = APP / "run_data"
    csr = generate_csr(wd)

    # scp path: Phi reads/writes /tmp (ramfs)
    r_scp = run_path("scp→/tmp", csr, "/tmp", wd)

    # VirtIO path: Phi reads/writes /media/vda (VirtIO block)
    r_vio = run_path("VirtIO→/media/vda", csr, "/media/vda", wd)

    print("\n" + "=" * 60)
    print("  对比")
    print("=" * 60)
    print(f"  {'':<18} {'scp→/tmp':>12} {'VirtIO→/media/vda':>18}")
    print(f"  {'scp 上传':<18} {r_scp['t_scp_in']:>11.2f}s  {r_vio['t_scp_in']:>17.2f}s")
    print(f"  {'Phi 分块':<18} {r_scp['t_phi']:>11.2f}s  {r_vio['t_phi']:>17.2f}s")
    print(f"  {'scp 下载':<18} {r_scp['t_scp_out']:>11.2f}s  {r_vio['t_scp_out']:>17.2f}s")
    print(f"  {'VE SpMV':<18} {r_scp['t_ve']:>11.2f}s  {r_vio['t_ve']:>17.2f}s")
    print(f"  {'总计':<18} {r_scp['t_total']:>11.2f}s  {r_vio['t_total']:>17.2f}s")
    print(f"  {'max_diff':<18} {r_scp['max_diff']:>11.1e}  {r_vio['max_diff']:>17.1e}")


if __name__ == "__main__":
    main()
