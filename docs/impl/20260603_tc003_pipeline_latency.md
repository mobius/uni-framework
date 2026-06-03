# TC-HETERO-003: 流水线延迟 — 实现记录

> 日期: 2026-06-03
> 计划: `docs/plan/20260603_phase3_bench_plan.md`

---

## 1. 新增文件

```
uni/
├── scripts/bench_pipeline_latency.py         # TC-003 对比脚本 (新增)
└── docs/impl/20260603_tc003_pipeline_latency.md  # 本文件
```

## 2. 方法

对比两条等长数据流水线的端到端延迟:

- **链 A (纯 VE)**: gen → VE1(NLC dgemm) → VE2(scale) → VE3(transpose)
- **链 B (含 Phi)**: gen → VE1(NLC dgemm) → VE2(scale) → Phi(stats)

二者差别仅在第三步: VE3 transpose vs Phi stats。

## 3. 实测结果

```
链 A (纯 VE):
  gen          [host] 0.11s
  dgemm        [ve1 ] 0.11s
  scale        [ve2 ] 0.10s
  transpose    [ve3 ] 0.10s
  总延迟: 0.41s

链 B (含 Phi):
  gen          [host] 0.02s
  dgemm        [ve1 ] 0.10s
  scale        [ve2 ] 0.10s
  stats        [phi0] 2.54s
  总延迟: 2.75s
```

| 指标 | 值 |
|------|-----|
| 纯 VE 总延迟 | 0.41s |
| 含 Phi 总延迟 | 2.75s |
| Phi 绝对开销 | 2.34s |
| Overhead | **569%** |
| 通过标准 (≤20%) | ⚠️ 未达 |

## 4. 分析

- **Phi 是延迟炸弹**: micnativeloadex 启动 (~2s) 占 Phi 总耗时 92%
- **VE 任务极快**: dgemm/scale/transpose 各 0.10-0.11s，I/O 开销 <0.05s
- **569% overhead 不是 PCIe 问题**: V1 验证已确认 Phi 启动开销 ~1.8-2.5s，计算本身仅 <0.5s
- **适用场景**: Phi 适合长时批处理（数据加载后持续计算），不适合短交互/流水线中转

## 5. 下一步

TC-HETERO-004 (VE-MPI 扩展性) — AllReduce 1/2/3 卡扩展效率测试。
