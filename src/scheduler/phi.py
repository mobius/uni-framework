"""
调度层 — Phi 7120P 管理
phi.py — 编译 (podman ICC) + 部署 + 执行 + 结果解析
"""

import subprocess
import os
import tempfile
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parent.parent.parent  # uni/
PHI_KERNEL_SRC = WORK_ROOT / "src" / "kernels" / "phi" / "peak_fp64.c"
PHI_KERNEL_BIN = WORK_ROOT / "src" / "kernels" / "phi" / "peak_fp64.mic"
CONTAINER = "centos7-phi-dev"


def compile_phi_kernel() -> bool:
    """在 podman 容器中编译 Phi 内核"""
    src_path = PHI_KERNEL_SRC
    if not src_path.exists():
        print(f"[phi] 内核源码不存在: {src_path}")
        return False

    # 复制到临时目录供容器访问
    cmd = (
        f"podman cp {src_path} {CONTAINER}:/tmp/peak_fp64.c && "
        f"podman exec {CONTAINER} bash -c '"
        f"source /opt/intel/bin/compilervars.sh intel64 && "
        f"icc -std=c99 -mmic -O3 -openmp -o /tmp/peak_fp64.mic /tmp/peak_fp64.c' && "
        f"podman cp {CONTAINER}:/tmp/peak_fp64.mic {PHI_KERNEL_BIN}"
    )

    print("[phi] 编译内核 (ICC via podman)...")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        print(f"[phi] 编译失败:\n{result.stderr}")
        return False

    print(f"[phi] 编译成功 → {PHI_KERNEL_BIN}")
    return True


def run_phi_kernel() -> dict:
    """在 Phi 上运行内核，返回结果"""
    if not PHI_KERNEL_BIN.exists():
        print("[phi] 内核二进制不存在，尝试编译...")
        if not compile_phi_kernel():
            return {"status": "fail", "error": "compile failed"}

    print("[phi] 运行 micnativeloadex...")

    # 设置 MIC 运行时库路径
    mic_lib_path = WORK_ROOT.parent / "intel_phi" / "icc_mic_libs"
    env = os.environ.copy()
    if mic_lib_path.is_dir():
        env["SINK_LD_LIBRARY_PATH"] = str(mic_lib_path)

    cmd = f"micnativeloadex {PHI_KERNEL_BIN} -d 0 -t 60"
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=120,
        env=env
    )

    stdout = result.stdout
    stderr = result.stderr

    # 解析输出
    gflops = _parse_gflops(stdout)
    theory_pct = _parse_theory_pct(stdout)
    elapsed = _parse_elapsed(stdout)

    status = "pass" if (gflops and gflops > 400) else "fail"

    return {
        "status": status,
        "gflops": gflops,
        "theory_pct": theory_pct,
        "elapsed_sec": elapsed,
        "stdout": stdout,
        "stderr": stderr,
    }


def _parse_gflops(text: str) -> float:
    for line in text.splitlines():
        if "GFLOPS" in line or "GFlops" in line:
            try:
                return float(line.split(":")[-1].strip().split()[0])
            except (ValueError, IndexError):
                pass
    return 0.0


def _parse_theory_pct(text: str) -> float:
    for line in text.splitlines():
        if "% of theory" in line or "%" in line and "theory" in line.lower():
            try:
                import re
                m = re.search(r'(\d+\.?\d*)\s*%', line)
                if m:
                    return float(m.group(1))
            except (ValueError, IndexError):
                pass
    return 0.0


def _parse_elapsed(text: str) -> float:
    for line in text.splitlines():
        if "Elapsed" in line or "Time" in line:
            try:
                import re
                m = re.search(r'(\d+\.?\d*)\s*sec', line)
                if m:
                    return float(m.group(1))
            except (ValueError, IndexError):
                pass
    return 0.0
