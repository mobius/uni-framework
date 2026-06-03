#!/usr/bin/env python3
"""
spmv_app.py — 异构 SpMV: Phi CSR 分块 + 3×VE 并行乘法

Pipeline:
  1. Host: 生成随机稀疏矩阵 (CSR 格式), numpy 计算 y_ref
  2. Host → scp → Phi: 上传 CSR 到 mic0:/tmp/
  3. Phi: 244 线程并行 CSR 列分块, 写入 mic0:/tmp/spmv_block_*.bin
  4. Phi → scp → Host: 下载分块文件
  5. VE1/2/3: 并行计算各自分块的 SpMV
  6. Host: 合并 y_partial → y_merged, 对比 y_ref

Usage:
  ./env/.venv/bin/python3 src/apps/hetero_spmv/spmv_app.py [N] [density]
"""

import sys, os, time, struct, asyncio, subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent.parent
APP_DIR = PROJECT / "src" / "apps" / "hetero_spmv"
MIC_LIBS = PROJECT.parent / "intel_phi" / "icc_mic_libs"

N = 4096
DENSITY = 0.01


def shell(cmd, timeout=120, env=None):
    t0 = time.time()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip(), time.time() - t0


def compile_phi():
    src = APP_DIR / "phi" / "csr_partition.c"
    out = APP_DIR / "phi" / "csr_partition.mic"
    if out.exists():
        return True
    print("[compile] Phi CSR partition (ICC via podman)...")
    rc, _, err, _ = shell(
        f"podman start centos7-phi-dev 2>/dev/null && "
        f"podman cp {src} centos7-phi-dev:/tmp/csr_partition.c && "
        f"podman exec centos7-phi-dev bash -c '"
        f"source /opt/intel/bin/compilervars.sh intel64 && "
        f"icc -std=c99 -mmic -O3 -openmp -o /tmp/csr_partition.mic /tmp/csr_partition.c' && "
        f"podman cp centos7-phi-dev:/tmp/csr_partition.mic {out}")
    if rc != 0:
        print(f"  FAILED:\n{err}")
    return rc == 0


def compile_ve():
    src = APP_DIR / "ve" / "blocked_spmv.c"
    out = APP_DIR / "ve" / "blocked_spmv_ve"
    if out.exists():
        return True
    rc, _, err, _ = shell(f"ncc -O3 -fopenmp -o {out} {src}")
    return rc == 0


def generate_csr(N: int, density: float, wd: Path) -> dict:
    """Generate random CSR matrix + compute y_ref"""
    import numpy as np
    rng = np.random.default_rng(42)
    nnz = max(int(N * N * density), 1)

    rows = rng.integers(0, N, nnz, dtype=np.int32)
    cols = rng.integers(0, N, nnz, dtype=np.int32)
    vals = rng.normal(0, 1.0, nnz).astype(np.float64)

    order = np.argsort(rows)
    rows, cols, vals = rows[order], cols[order], vals[order]

    row_ptr = np.zeros(N + 1, dtype=np.int32)
    np.add.at(row_ptr, rows + 1, 1)
    np.cumsum(row_ptr, out=row_ptr)

    nnz = int(row_ptr[-1])
    rows, cols, vals = rows[:nnz], cols[:nnz], vals[:nnz]
    x = rng.normal(0, 1.0, N).astype(np.float64)

    # Reference
    y_ref = np.zeros(N)
    for j in range(nnz):
        y_ref[rows[j]] += vals[j] * x[cols[j]]

    # Write CSR
    wd.mkdir(parents=True, exist_ok=True)
    csr_path = wd / "input.csr"
    with open(csr_path, "wb") as f:
        f.write(struct.pack("i", N))
        f.write(struct.pack("i", nnz))
        f.write(row_ptr.tobytes())
        f.write(cols.astype(np.int32).tobytes())
        f.write(vals.tobytes())
        f.write(x.tobytes())

    (wd / "y_ref.bin").write_bytes(struct.pack("i", N) + y_ref.tobytes())

    print(f"[host] N={N}, nnz={nnz} ({density*100:.1f}%), "
          f"y_ref checksum={y_ref.sum():.3f}")
    return {"N": N, "nnz": nnz, "csr_path": csr_path}


def run_phi_partition(csr_path: Path, wd: Path) -> tuple[float, dict]:
    """scp CSR → Phi partition → scp blocks back"""
    import uuid
    exe = APP_DIR / "phi" / "csr_partition.mic"
    env = os.environ.copy()
    if MIC_LIBS.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(MIC_LIBS)

    # Unique run ID to avoid stale files
    run_id = uuid.uuid4().hex[:8]
    remote_prefix = f"/tmp/spmv_{run_id}"
    remote_in = f"{remote_prefix}_input.csr"
    remote_full = f"{remote_prefix}_full.csr"

    # 1. scp CSR to Phi
    print(f"[phi] scp input → mic0:{remote_in}")
    t0 = time.time()
    rc, _, err, _ = shell(f"scp {csr_path} mic0:{remote_in}", timeout=30)
    if rc != 0:
        print(f"  scp FAILED: {err}")
        return 0, {}
    print(f"  ✓ {time.time()-t0:.1f}s")

    # 2. Run Phi partition
    print("[phi] CSR partition (244 threads)...")
    t0 = time.time()
    args = f'"{remote_in} {remote_prefix}"'
    rc, out, err, _ = shell(
        f"micnativeloadex {exe} -d 0 -t 60 -a {args}", timeout=120, env=env)
    phi_time = time.time() - t0
    for line in (out + err).splitlines():
        if line.strip():
            print(f"    {line.strip()}")
    if rc != 0:
        print(f"  Phi FAILED: {err}")
        return phi_time, {}
    print(f"  ✓ {phi_time:.1f}s")

    # 3. scp blocks back
    blocks = []
    meta = {}
    print(f"[phi] scp blocks ← mic0:{remote_prefix}_*.bin")
    for i in range(3):
        remote = f"mic0:{remote_prefix}_block_{i}.bin"
        local = wd / f"block_{i}.bin"
        rc, _, err, _ = shell(f"scp {remote} {local}", timeout=30)
        if rc == 0 and local.exists():
            blocks.append(local)
            data = local.read_bytes()
            meta["N"] = struct.unpack("i", data[:4])[0]
            meta["nnz"] = struct.unpack("i", data[4:8])[0]
            print(f"  block_{i}: {local.stat().st_size//1024}KB ✓")
        else:
            print(f"  block_{i}: FAILED - {err}")

    # 4. scp full CSR
    rc, _, err, _ = shell(f"scp mic0:{remote_full} {wd / 'input.csr'}", timeout=30)
    if rc == 0:
        print(f"  full_csr: {(wd/'input.csr').stat().st_size//1024}KB ✓")

    return phi_time, {"N": meta.get("N", N), "blocks": blocks}


async def run_ve_spmv(ve_id: int, block_path: Path, out_path: Path) -> dict:
    exe = APP_DIR / "ve" / "blocked_spmv_ve"
    cmd = f"/opt/nec/ve/bin/ve_exec -N {ve_id} {exe} {block_path} {out_path}"
    t0 = time.time()
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    elapsed = time.time() - t0

    text = stdout.decode() + stderr.decode()
    nnz, bw = 0, 0.0
    for token in text.split():
        if token.startswith("nnz="):
            nnz = int(token.split("=")[1])
        if token.startswith("BW="):
            bw = float(token.rstrip("_GB/s").split("=")[1])
    print(f"    {text.strip()[:120]}")
    return {"device": f"ve{ve_id}", "elapsed": elapsed, "nnz": nnz, "bw": bw}


def merge_and_verify(wd: Path) -> tuple[bool, float, float]:
    import numpy as np
    ref_data = (wd / "y_ref.bin").read_bytes()
    N = struct.unpack("i", ref_data[:4])[0]
    y_ref = np.frombuffer(ref_data[4:], dtype=np.float64)

    y_merged = np.zeros(N)
    for i in range(3):
        data = (wd / f"y_partial_{i}.bin").read_bytes()
        y_merged += np.frombuffer(data[4:], dtype=np.float64)

    max_diff = float(np.abs(y_merged - y_ref).max())
    mean_diff = float(np.abs(y_merged - y_ref).mean())
    return max_diff < 1e-10, max_diff, mean_diff


async def main():
    print("=" * 60)
    print("  异构 SpMV: Phi CSR 分块 + 3×VE 并行乘法")
    print(f"  N={N}  density={DENSITY}")
    print("=" * 60)

    wd = APP_DIR / "run_data"

    if not compile_phi() or not compile_ve():
        print("❌ 编译失败")
        return

    # 1. Generate CSR
    print()
    meta = generate_csr(N, DENSITY, wd)

    # 2. Phi partition (scp → Phi → scp)
    print()
    phi_time, phi_meta = run_phi_partition(meta["csr_path"], wd)
    if not phi_meta.get("blocks"):
        print("❌ Phi 分块失败")
        return

    # 3. VE parallel SpMV
    print(f"\n[ve] 并行分块 SpMV...")
    t0 = time.time()
    tasks = [run_ve_spmv(i+1, wd / f"block_{i}.bin", wd / f"y_partial_{i}.bin")
             for i in range(3)]
    ve_results = await asyncio.gather(*tasks)
    ve_time = time.time() - t0

    total_nnz = 0
    for r in ve_results:
        total_nnz += r["nnz"]
        print(f"  {r['device']}: nnz={r['nnz']} {r['elapsed']:.4f}s BW={r['bw']:.2f} GB/s")

    # 4. Verify
    print()
    ok, max_diff, mean_diff = merge_and_verify(wd)

    print("=" * 60)
    print("  结果")
    print("=" * 60)
    print(f"  矩阵: N={N}, nnz={meta['nnz']}")
    print(f"  Phi 分块: {phi_time:.1f}s (scp+partition+scp)")
    print(f"  VE 并行:  {ve_time:.3f}s")
    print(f"  max_diff:  {max_diff:.2e}")
    print(f"  mean_diff: {mean_diff:.2e}")
    print(f"  正确性:    {'✅ 通过' if ok else '❌ 失败'}")


if __name__ == "__main__":
    asyncio.run(main())
