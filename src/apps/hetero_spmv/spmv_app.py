#!/usr/bin/env python3
"""
spmv_app.py — 异构 SpMV: Host 分块 + 3×VE 并行乘法

Pipeline:
  1. Host: 生成随机稀疏矩阵 (CSR 格式), numpy 计算 y_ref
  2. Host: 按列分 3 块写入文件
  3. VE1/2/3: 并行计算各自分块的 SpMV
  4. Host: 合并 y_partial → y_merged, 对比 y_ref

Usage:
  ./env/.venv/bin/python3 src/apps/hetero_spmv/spmv_app.py [N] [density]
"""

import sys, os, time, struct, asyncio, subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent.parent
APP_DIR = PROJECT / "src" / "apps" / "hetero_spmv"

N = 4096
DENSITY = 0.01


def shell(cmd, timeout=120, env=None):
    t0 = time.time()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=timeout, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip(), time.time() - t0


def compile_ve():
    src = APP_DIR / "ve" / "blocked_spmv.c"
    out = APP_DIR / "ve" / "blocked_spmv_ve"
    if out.exists():
        return True
    rc, _, err, _ = shell(f"ncc -O3 -fopenmp -o {out} {src}")
    if rc != 0:
        print(f"[compile] FAILED: {err}")
    return rc == 0


def generate_and_partition(N: int, density: float, wd: Path) -> dict:
    """Generate CSR, partition by columns into 3 blocks, compute y_ref"""
    import numpy as np
    rng = np.random.default_rng(42)
    nnz = max(int(N * N * density), 1)

    rows = rng.integers(0, N, nnz)
    cols = rng.integers(0, N, nnz)
    vals = rng.normal(0, 1.0, nnz).astype(np.float64)

    order = np.argsort(rows)
    rows, cols, vals = rows[order], cols[order], vals[order]

    row_ptr = np.zeros(N + 1, dtype=np.int32)
    np.add.at(row_ptr, rows + 1, 1)
    np.cumsum(row_ptr, out=row_ptr)

    nnz = row_ptr[-1]
    rows, cols, vals = rows[:nnz], cols[:nnz], vals[:nnz]

    x = rng.normal(0, 1.0, N).astype(np.float64)

    # Reference: y = A @ x
    y_ref = np.zeros(N)
    for j in range(nnz):
        y_ref[rows[j]] += vals[j] * x[cols[j]]
    ref_checksum = float(y_ref.sum())

    # Partition: 3 equal column ranges
    chunk = (N + 2) // 3
    blocks = []
    for v in range(3):
        c_start = v * chunk
        c_end = N if v == 2 else (v + 1) * chunk

        # Filter non-zeros in column range
        mask = (cols >= c_start) & (cols < c_end)
        idx = np.where(mask)[0]
        b_nnz = len(idx)
        b_rows = rows[idx]
        b_cols = cols[idx]
        b_vals = vals[idx]

        # Build row_ptr for this block
        b_row_ptr = np.zeros(N + 1, dtype=np.int32)
        np.add.at(b_row_ptr, b_rows + 1, 1)
        np.cumsum(b_row_ptr, out=b_row_ptr)

        blocks.append({
            "nnz": b_nnz, "col_start": c_start, "col_end": c_end,
            "row_ptr": b_row_ptr, "cols": b_cols, "vals": b_vals,
        })

    print(f"[host] N={N}, nnz={nnz} ({density*100:.1f}%), "
          f"y_ref checksum={ref_checksum:.3f}")

    # Write blocks + full CSR
    wd.mkdir(parents=True, exist_ok=True)
    for v, b in enumerate(blocks):
        path = wd / f"block_{v}.bin"
        with open(path, "wb") as f:
            f.write(struct.pack("i", N))
            f.write(struct.pack("i", b["nnz"]))
            f.write(struct.pack("i", b["col_start"]))
            f.write(struct.pack("i", b["col_end"]))
            f.write(b["row_ptr"].astype(np.int32).tobytes())
            f.write(b["cols"].astype(np.int32).tobytes())
            f.write(b["vals"].astype(np.float64).tobytes())
            f.write(x.astype(np.float64).tobytes())
        print(f"  block {v}: nnz={b['nnz']} cols=[{b['col_start']},{b['col_end']})")

    # Save full CSR + x + y_ref for verification
    full_path = wd / "input.csr"
    with open(full_path, "wb") as f:
        f.write(struct.pack("i", N))
        f.write(struct.pack("i", nnz))
        f.write(row_ptr.tobytes())
        f.write(cols.tobytes())
        f.write(vals.tobytes())
        f.write(x.tobytes())

    (wd / "y_ref.bin").write_bytes(struct.pack("i", N) + y_ref.tobytes())
    (wd / "x.bin").write_bytes(struct.pack("i", N) + x.tobytes())

    return {"N": N, "nnz": nnz, "blocks": blocks, "y_ref_checksum": ref_checksum}


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
    print("  异构 SpMV: Host 列分块 + 3×VE 并行")
    print(f"  N={N}  density={DENSITY}")
    print("=" * 60)

    wd = APP_DIR / "run_data"
    if not compile_ve():
        print("❌ VE 编译失败")
        return

    # 1. Generate + partition
    print()
    meta = generate_and_partition(N, DENSITY, wd)

    # 2. VE parallel SpMV
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

    # 3. Verify
    print()
    ok, max_diff, mean_diff = merge_and_verify(wd)

    print("=" * 60)
    print("  结果")
    print("=" * 60)
    print(f"  矩阵: N={N}, nnz={meta['nnz']}")
    print(f"  分块: VE0={meta['blocks'][0]['nnz']} VE1={meta['blocks'][1]['nnz']} "
          f"VE2={meta['blocks'][2]['nnz']}")
    print(f"  VE 并行耗时: {ve_time:.3f}s")
    print(f"  max_diff:  {max_diff:.2e}")
    print(f"  mean_diff: {mean_diff:.2e}")
    print(f"  正确性:    {'✅ 通过' if ok else '❌ 失败'}")


if __name__ == "__main__":
    asyncio.run(main())
