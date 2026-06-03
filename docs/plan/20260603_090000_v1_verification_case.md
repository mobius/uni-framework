# 验证案例 V1: 四卡并行 FP64 基线算力验证

> 规划日期: 2026-06-03
> 目标: 一次性验证 Phi + 3×VE 全部可用，测量每卡 FP64 算力基线
> 前置: `uni/docs/research/20260601_090918_heterogeneous_system_analysis.md`

---

## 目标

用一个最小可运行的案例，验证异构调度层的基本能力：

1. 设备发现 — 自动检测 Phi 和 3 张 VE 卡
2. 内核执行 — 每卡独立运行 FP64 FMA 峰值测试
3. 并行调度 — Python asyncio 并行启动 4 卡
4. 结果汇总 — 报告每卡 GFLOPS 和总总算力
5. 硬件状态 — 记录温度、功耗基线

## 设计

### 架构

```
run_verify.py (入口)
  │
  ├─ devices.py  → 发现 Phi + VE0/1/2, 返回 DeviceInfo
  ├─ phi.py     → 编译/部署/运行 Phi 内核
  ├─ ve.py      → 编译/部署/运行 VE 内核
  └─ 汇总报告    → 表格化输出 + 判断通过/失败
```

### 内核

| 卡 | 内核 | 编译 | 预期 GFLOPS |
|----|------|------|------------|
| Phi | FP64 FMA (KNC intrinsics, 16 acc) | ICC in podman | 550-580 |
| VE×3 | FP64 FMA (OpenMP 8核, ncc) | ncc -fopenmp | 200-400/卡 |

> VE 端使用简化 FMA 内核而非 nfort DGEMM，因为 nfort 需要 Fortran 链接，复杂度较高。FMA 内核更纯粹地测试硬件状态。

### 通过标准

| 指标 | 标准 |
|------|------|
| 设备发现 | 1 Phi + 3 VE |
| Phi GFLOPS | ≥ 400 |
| VE GFLOPS/卡 | ≥ 100 |
| 总 GFLOPS | ≥ 700 |
| 执行时间 | < 30 秒 |
| 无运行时错误 | 全部 PASS |

---

## 实现清单

1. `src/scheduler/devices.py` — DeviceInfo + discover()
2. `src/scheduler/phi.py` — 编译 + micnativeloadex 执行
3. `src/scheduler/ve.py` — 编译 + ve_exec 执行
4. `src/kernels/phi/peak_fp64.c` — 复用已有代码
5. `src/kernels/ve/peak_fp64.c` — 新写 VE FMA 内核
6. `scripts/run_verify.py` — 入口脚本
7. `docs/impl/20260603_xxxxxx_v1_verification.md` — 实现记录
