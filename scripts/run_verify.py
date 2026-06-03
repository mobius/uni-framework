#!/usr/bin/env python3
"""
run_verify.py — 异构计算系统验证入口
四卡并行 FP64 基线算力验证: Phi + 3×VE

用法:
    python scripts/run_verify.py
    python scripts/run_verify.py --compile-only   # 仅编译
    python scripts/run_verify.py --verbose         # 详细输出
"""

import sys
import os
import asyncio
import time
import argparse
from pathlib import Path

# 将 src/ 加入 Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scheduler.devices import discover_all, DeviceInfo
from scheduler.phi import compile_phi_kernel, run_phi_kernel
from scheduler.ve import compile_ve_kernel, run_ve_kernel


def banner(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def report(devices: list[DeviceInfo]):
    """汇总报告"""
    banner("验证结果")

    total_gflops = sum(d.gflops for d in devices)
    total_pass = sum(1 for d in devices if d.status == "pass")
    total_dev = len(devices)

    print(f"\n{'设备':<8} {'类型':<6} {'NUMA':<6} {'GFLOPS':<12} {'用时':<10} {'状态'}")
    print("-" * 60)
    for d in devices:
        gflops_str = f"{d.gflops:.1f}" if d.gflops else "N/A"
        elapsed_str = f"{d.elapsed_sec:.2f}s" if d.elapsed_sec else "N/A"
        status_icon = "✅" if d.status == "pass" else "❌"
        print(f"{d.name:<8} {d.kind:<6} {d.numa_node:<6} {gflops_str:<12} {elapsed_str:<10} {status_icon}")

    print("-" * 60)
    print(f"{'总计':<8} {'':<6} {'':<6} {total_gflops:.1f}")

    banner("判定")
    print(f"  设备: {total_pass}/{total_dev} 通过")
    print(f"  总算力: {total_gflops:.1f} GFLOPS")

    # 通过标准
    checks = []
    checks.append(("Phi ≥ 400 GFLOPS", _check_phi(devices)))
    checks.append(("VE ×3 全部 ≥ 100 GFLOPS", _check_ve(devices)))
    checks.append(("总 ≥ 700 GFLOPS", total_gflops >= 700))

    all_ok = True
    for desc, ok in checks:
        icon = "✅" if ok else "❌"
        print(f"  {icon} {desc}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\n  🎉 全部通过 — 异构计算系统基线验证成功")
    else:
        print("\n  ⚠️  部分未通过 — 请检查设备日志")

    return all_ok


def _check_phi(devices: list[DeviceInfo]) -> bool:
    for d in devices:
        if d.kind == "phi":
            return d.gflops >= 400
    return False


def _check_ve(devices: list[DeviceInfo]) -> bool:
    ves = [d for d in devices if d.kind == "ve"]
    if len(ves) < 3:
        return False
    return all(d.gflops >= 100 for d in ves)


async def main():
    parser = argparse.ArgumentParser(description="异构计算系统验证")
    parser.add_argument("--compile-only", action="store_true", help="仅编译内核")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--phi-only", action="store_true", help="仅测试 Phi")
    parser.add_argument("--ve-only", action="store_true", help="仅测试 VE")
    args = parser.parse_args()

    # ── Step 1: 设备发现 ──
    banner("Step 1: 设备发现")

    devices = discover_all()
    if not devices:
        print("❌ 未发现任何加速卡")
        return

    print(f"\n{'设备':<8} {'类型':<6} {'PCIe地址':<16} {'NUMA':<6} {'在线'}")
    print("-" * 50)
    for d in devices:
        online_str = "✅" if d.online else "❌"
        print(f"{d.name:<8} {d.kind:<6} {d.pcie_addr:<16} {d.numa_node:<6} {online_str}")

    phi_devices = [d for d in devices if d.kind == "phi"]
    ve_devices = [d for d in devices if d.kind == "ve"]

    if not args.ve_only and not phi_devices:
        print("❌ 未发现 Phi 卡")
    if not args.phi_only and len(ve_devices) < 3:
        print(f"⚠️  仅发现 {len(ve_devices)} 张 VE 卡 (期望 3)")

    # ── Step 2: 编译内核 ──
    banner("Step 2: 编译内核")

    # 启动 podman 容器 (如有 phi)
    if phi_devices and not args.ve_only:
        print("[phi] 检查 podman 容器...")
        os.system("podman start centos7-phi-dev 2>/dev/null || true")

    # 并行编译 (顺序执行，因为 podman 和 ncc 互不干扰)
    phi_ok = True
    ve_ok = True

    if phi_devices and not args.ve_only:
        phi_ok = compile_phi_kernel()
    if ve_devices and not args.phi_only:
        ve_ok = compile_ve_kernel()

    if args.compile_only:
        print("\n编译完成。")
        return

    if not phi_ok and phi_devices and not args.ve_only:
        print("❌ Phi 内核编译失败")
    if not ve_ok and ve_devices and not args.phi_only:
        print("❌ VE 内核编译失败")

    # ── Step 3: 并行运行 ──
    banner("Step 3: 并行执行")

    async def run_phi_async(dev: DeviceInfo):
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_phi_kernel)
        dev.gflops = result.get("gflops", 0.0)
        dev.elapsed_sec = result.get("elapsed_sec", 0.0)
        dev.status = result.get("status", "fail")
        dev.stdout = result.get("stdout", "")
        dev.stderr = result.get("stderr", "")
        return result

    async def run_ve_async(dev: DeviceInfo):
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, run_ve_kernel, dev.ve_id, dev.numa_node
        )
        dev.gflops = result.get("gflops", 0.0)
        dev.elapsed_sec = result.get("elapsed_sec", 0.0)
        dev.status = result.get("status", "fail")
        dev.stdout = result.get("stdout", "")
        dev.stderr = result.get("stderr", "")
        return result

    tasks = []
    for d in devices:
        if d.kind == "phi" and not args.ve_only:
            tasks.append(run_phi_async(d))
        elif d.kind == "ve" and not args.phi_only:
            tasks.append(run_ve_async(d))

    start_time = time.time()
    results = await asyncio.gather(*tasks)
    total_time = time.time() - start_time

    print(f"\n全部内核执行完成，耗时 {total_time:.1f}s")

    # 详细输出
    if args.verbose:
        for d in devices:
            if d.stdout:
                print(f"\n--- {d.name} stdout ---")
                print(d.stdout[:500])
            if d.stderr:
                print(f"\n--- {d.name} stderr ---")
                print(d.stderr[:500])

    # ── Step 4: 汇总报告 ──
    report(devices)


if __name__ == "__main__":
    asyncio.run(main())
