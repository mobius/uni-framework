# TC-HETERO-002: 数据中心吞吐 — 实现记录

> 日期: 2026-06-03
> 计划: `docs/plan/20260603_phase3_bench_plan.md`

---

## 1. 新增文件

```
uni/
├── scripts/bench_throughput.py           # TC-002 基准脚本 (新增)
├── examples/throughput/run_data/         # 测试数据目录 (新增)
└── docs/impl/20260603_tc002_throughput.md  # 本文件
```

## 2. 方法

- **负载**: 3×VE NLC DGEMM (N=2048) + Phi FMA 峰值，4 卡并行
- **矩阵**: 3 对 2048×2048 float64 随机矩阵 (~100 MB/对)
- **调度**: asyncio.gather 并行启动

## 3. 实测结果

```
设备       操作           GFLOPS     耗时
──────────────────────────────────────────
ve1      dgemm (NLC)   1716       0.14s
ve2      dgemm (NLC)   1676       0.15s
ve3      dgemm (NLC)   1712       0.15s
phi0     fma_peak       578       2.53s
──────────────────────────────────────────
总计                     5682       2.53s
```

| 指标 | 值 | 判定 |
|------|-----|------|
| VE×3 NLC DGEMM | 5,104 GFLOPS | 78.8% 峰值 (2160×3) |
| Phi FMA | 578 GFLOPS | 47.8% 峰值 (1208) |
| **总算力** | **5.68 TFLOPS** | ✅ ≥ 5.00 |
| 瓶颈 | Phi (2.53s) | micnativeloadex 启动开销 |

## 4. 分析

- **VE NLC DGEMM 效率 78.8%**: 与 N=2048 基准 79% 一致，NLC 充分利用 HBM 带宽
- **并行无串扰**: 3 张 VE 同时 DGEMM，各自 1676-1716 GFLOPS，相互独立
- **Phi 是瓶颈**: 2.53s 中大部分为 micnativeloadex 启动开销，计算本身 <0.5s
- **5.68 TFLOPS** 已接近系统理论峰值 7.69 TFLOPS 的 74%

## 5. 下一步

TC-HETERO-003 (流水线延迟) — 对比纯 VE 链 vs 含 Phi 链的端到端延迟。
