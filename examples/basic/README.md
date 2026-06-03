# 示例 1: 四卡并行基线验证 (Basic)

演示最基本的异构调度能力：设备发现 → 并行执行 → 结果汇总。

## 演示内容

1. 自动发现 1×Phi + 3×VE 计算卡
2. 每卡独立运行 FP64 FMA 峰值测试
3. 4 卡并行启动，Python asyncio 调度
4. 表格化汇总报告 + 通过/失败判定

## 运行

```bash
cd uni
python3.11 scripts/run_verify.py
# --compile-only  仅编译内核
# -v              查看每卡详细输出
# --phi-only      仅测试 Phi
# --ve-only       仅测试 VE
```

## 预期输出

```
设备       类型     NUMA   GFLOPS       用时         状态
phi0     phi    0      577.6        0.45s      ✅
ve1      ve     0      902.1        0.08s      ✅
ve2      ve     1      907.1        0.08s      ✅
ve3      ve     1      900.8        0.08s      ✅
────────────────────────────────────────────────────
总计                     3,287.6

  ✅ Phi ≥ 400 GFLOPS
  ✅ VE ×3 全部 ≥ 100 GFLOPS
  ✅ 总 ≥ 700 GFLOPS
  🎉 全部通过 — 异构计算系统基线验证成功
```

## 关键代码路径

| 模块 | 路径 | 用途 |
|------|------|------|
| 设备发现 | `src/scheduler/devices.py` | lspci + sysfs |
| Phi 管理 | `src/scheduler/phi.py` | ICC 编译 + micnativeloadex |
| VE 管理 | `src/scheduler/ve.py` | ncc 编译 + ve_exec |
| Phi 内核 | `src/kernels/phi/peak_fp64.c` | KNC intrinsics FMA |
| VE 内核 | `src/kernels/ve/peak_fp64.c` | 数组 FMA 自动向量化 |
| 入口 | `scripts/run_verify.py` | 一键运行 |

## 涉及的概念

- 设备枚举与健康检查
- 独立编译链 (ICC via podman, ncc native)
- 并行任务调度 (asyncio.gather)
- NUMA 拓扑感知 (读取, 未绑定)
- 结果解析与汇总
