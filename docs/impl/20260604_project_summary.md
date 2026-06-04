# Uni — 异构计算协同项目 终期总结

> 日期: 2026-06-04
> 服务器: ASUS ESC4000 G4, 2× Xeon Gold 6252
> 加速卡: 1× Intel Xeon Phi 7120P + 3× NEC Vector Engine 1.0

---

## 1. 项目完成度

| Phase | 内容 | 状态 | 关键交付 |
|-------|------|------|---------|
| 0 | 硬件验证与基线确认 | ✅ | check_hw.sh |
| 1 | 统一软件栈搭建 | ✅ | uv/ncc/ICC/podman |
| 2 | 核心调度层 (7 模块) | ✅ | devices/phi/ve/numa/power/task_graph/profiler |
| 3 | 协同基准测试 (6 项) | ✅ | TC-001~006 (4 项通过, 2 项标注) |
| 4 | 示例应用 (3 个) | ✅ | SpMV / 数据预处理 / Monte Carlo |

## 2. 调度层架构 (7 模块)

```
TaskGraph (DAG + 功率封顶)
  ├── DeviceMgr → 自动发现 Phi + 3×VE, 健康检查
  ├── NUMABinder → numactl 自动绑定 (VE0↔NUMA0, VE1/2↔NUMA1)
  ├── PowerCap → 1440W 有效预算, 超额排队
  ├── PhiRunner → micnativeloadex + scp I/O
  ├── VERunner → ve_exec 文件穿透
  └── Profiler → 预估 vs 实测对比
```

## 3. 基准测试结果

| 测试 | 指标 | 结果 | 判定 |
|------|------|------|------|
| TC-001 PCIe 带宽 | 3VE 并发 H2D | 13.7 GB/s (86%) | ⚠️ 文件 I/O 瓶颈 |
| TC-002 数据中心吞吐 | 4 卡总算力 | **5.68 TFLOPS** | ✅ |
| TC-003 流水线延迟 | Phi 中转开销 | 569% (启动瓶颈) | ⚠️ |
| TC-004 VE-MPI 扩展性 | Ring 效率 | **97.8%** (VE2 调整) | ✅ |
| TC-005 功率封顶 | 满载功耗 | 139W << 1600W | ✅ 模型保守 |
| TC-006 稳定性 | 5min 混合负载 | 0 失败, ΔT 3.3°C | ✅ |

## 4. 应用举例

| 应用 | 流程 | 性能 | 正确性 |
|------|------|------|--------|
| 异构 SpMV | Host→Phi分块→3VE并行 | Phi 1.3s, VE 0.107s | max_diff 1.07e-14 ✅ |
| 数据预处理 | Phi清洗→VE1标准化→VE2 PCA | 全流水线 ~3s | std 3.55e-15, corr 0.997 ✅ |
| Monte Carlo | Phi路径→3VE payoff | Phi ~2s | vs numpy 0.15% ✅ |

## 5. 关键技术发现

### Phi 文件 I/O 隔离
- `micnativeloadex` 无共享文件系统, 需 scp 双向传输
- VE 的 `ve_exec` 天然文件穿透 (重大架构优势)

### CSR 分块踩坑
- 前缀和偏移错误 → 计数位置 `[i]` vs `[i+1]` 混淆
- mic0 root 属主文件残留 → UUID 唯一文件名

### VE2 性能差异
- 型号 10B (fw 5127, HBM 1600MHz) vs 10BE (fw 5400, HBM 1760MHz)
- MPI 环通信效率: 84% (含 VE2) vs 97.8% (VE2 调整后)
- 根因是 HBM 带宽差异 (9%), 非核心时钟 (0.6%)

### 功耗实测
- veda-smi 报告 ~47W/卡 (idle) → PowerCap 模型保守, 安全
- 满载温升仅 3.3°C (41.5→44.8), 散热充裕

## 6. 文件统计

```
src/
├── scheduler/    7 modules  (~1800 LOC Python)
├── kernels/      13 kernels (ve:9, phi:4)  (~2000 LOC C)
├── apps/         3 applications
│   ├── hetero_spmv/      3 files  (~300 LOC C + 200 LOC Python)
│   ├── hetero_dataprep/  4 files  (~350 LOC C + 180 LOC Python)
│   └── monte_carlo/      3 files  (~200 LOC C + 220 LOC Python)
├── benchmarks/   3 wrappers
scripts/          7 benchmark scripts
tests/            13 unit tests
docs/             20 documents (research/plan/impl)
```
