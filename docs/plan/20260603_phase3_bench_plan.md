# Phase 3 协同基准测试套件 — 实施计划

> 日期: 2026-06-03
> 前置: Phase 2 收尾完成 (NUMA + PowerCap)
> 目标: 完成 6 项 TC-HETERO 基准测试，验证异构协同关键指标
> 预计: 3-4 天 (实际可缩短，因多项已有代码基础)

---

## 1. 现状评估

### 1.1 已有可复用能力

| 能力 | 来源 | 用于 |
|------|------|------|
| Basic 四卡并行 FMA | bench_all.py / run_verify.py | TC-002 |
| Multi-Task DAG 调度 | bench_all.py | TC-002/003 |
| Pipeline 串行链 | bench_all.py | TC-003 |
| Profiler 预估模型 | profiler.py | TC-002/003 (对比) |
| PowerCap 功率封顶 | power.py | TC-005 |
| NUMABinder | numa.py | 所有测试 |
| veda-smi (VE 功耗/温度) | 系统工具 | TC-005/006 |
| Intel RAPL (CPU 功耗) | sysfs | TC-005/006 |

### 1.2 需要新增的内核

| 内核 | 用途 | 语言/编译 | 复杂度 |
|------|------|----------|--------|
| `ve/pcie_bw.c` | VE 端 PCIe H2D/D2H 带宽测试 | C + ncc | 低 |
| `ve/mpi_allreduce.c` | VE MPI AllReduce 扩展性 | C + mpincc | 中 |
| `phi/pcie_bw.c` | Phi 端 PCIe 带宽测试 | C + ICC/podman | 中 (需容器) |

### 1.3 需要新增的基准脚本

| 脚本 | 对应测试 | 说明 |
|------|---------|------|
| `scripts/bench_pcie.py` | TC-HETERO-001 | PCIe 带宽压力: 4 卡并发 H2D/D2H |
| `scripts/bench_throughput.py` | TC-HETERO-002 | 数据中心吞吐: 4 卡 DGEMM 合计 |
| `scripts/bench_pipeline_latency.py` | TC-HETERO-003 | 流水线延迟: Phi→VE 中转开销 |
| `scripts/bench_mpi.py` | TC-HETERO-004 | VE-MPI 扩展性: AllReduce 1/2/3 卡 |
| `scripts/bench_power.py` | TC-HETERO-005 | 功率封顶: 逐步加载 + PowerCap 验证 |
| `scripts/bench_stability.py` | TC-HETERO-006 | 30min 稳定性: 混合负载 + 温控监控 |

---

## 2. 分项测试设计

### TC-HETERO-001: PCIe 带宽压力

**目标**: 4 卡同时 H2D/D2H 传输，测量总吞吐和争抢度
**通过标准**: H2D 总吞吐 ≥ 30 GB/s

**设计**:
- VE 内核: 分配 256MB buffer，H2D 写入 + D2H 读回，计时，报告 GB/s
- Phi 内核: 同逻辑，通过 micnativeloadex 参数传入 buffer 大小
- 脚本: asyncio 并发启动 4 卡 PCIe 测试，汇总总吞吐

**预估**:
- 单卡 H2D: ~10 GB/s (PCIe Gen3 ×16 理论 15.75 GB/s)
- 单卡 D2H: ~5.3 GB/s
- 4 卡合计 H2D: ~30-40 GB/s (受限于 PCIe switch/root complex)
- 争抢度: 4 卡并发 vs 单卡，衡量带宽下降比例

### TC-HETERO-002: 数据中心吞吐

**目标**: 4 卡独立 DGEMM 总计，验证线性扩展
**通过标准**: FP64 总算力 ≥ 5.0 TFLOPS

**设计**:
- 基本已在 bench_all.py Basic 中覆盖
- 扩展: 使用 NLC DGEMM (N=2048) + Phi FMA 峰值
- 并行启动: VE×3 DGEMM + Phi FMA
- 对比: 顺序 vs 并发，衡量串扰

**预估**:
- VE×3 NLC DGEMM (N=2048): 3 × 1711 = 5133 GFLOPS
- Phi FMA: 575 GFLOPS
- **合计: ~5.7 TFLOPS** ✅ (>5.0)

### TC-HETERO-003: 流水线延迟

**目标**: Phi→VE 数据中转的端到端开销
**通过标准**: overhead ≤ 20% vs 纯 VE 流水线

**设计**:
- 纯 VE 链: gen → VE1(dgemm) → VE2(scale) → VE3(transpose) → host(report)
- 含 Phi 链: gen → VE1(dgemm) → **Phi(stats)** → VE2(transpose) → host(report)
- 对比两个链的总延迟，计算 Phi 中转额外开销

**预估**:
- 纯 VE 链: ~0.3s (VE 任务 0.1s×3 + I/O)
- 含 Phi 链: ~2.0s (Phi micnativeloadex 启动 ~1.8s 是主要开销)
- Overhead: ~600% — 超标，但主要是 Phi 启动开销，非 PCIe 中转

### TC-HETERO-004: VE-MPI 扩展性

**目标**: 3 卡 MPI AllReduce 扩展效率
**通过标准**: 三卡扩展效率 ≥ 95%

**设计**:
- MPI 内核: 512MB float64 数组 AllReduce (SUM)
- 运行: mpirun -ve 0-2 -np 1/2/3
- 指标: 执行时间，计算加速比和效率

**预估**:
- 单卡: baseline
- 双卡: ~1.9× (VE0↔VE1 直连)
- 三卡: ~2.8× (ring 拓扑，VE2 需经 VE1 中转)
- 扩展效率: 2.8/3 = 93% — 边缘

### TC-HETERO-005: 功率封顶验证

**目标**: 验证 PowerCap 模块实际效果
**通过标准**: 功率封顶生效，无 OTP 触发

**设计**:
- Step 1: 空闲功耗基线 (veda-smi + RAPL)
- Step 2: 逐步加载 — VE1 → VE1+VE2 → VE1+VE2+VE3 → +Phi
- Step 3: 满载 10s，记录峰值功耗
- Step 4: PowerCap 限制 1200W，验证任务被排队
- 对比: 预估 vs 实测功耗

**预估功耗**:
- 空闲: VE 46W×3 + CPU idle ~50W + Phi idle ~50W = ~238W
- VE1 full: +280W = ~518W
- VE1+2 full: +560W = ~798W
- VE1+2+3 full: +840W = ~1078W
- All full: +280W (Phi) = ~1358W (含 CPU ~300W → ~1658W)
- 满载可能逼近甚至超过 1600W

### TC-HETERO-006: 30min 稳定性

**目标**: 持续运行 30 分钟无失败
**通过标准**: 温度稳定，无降频，0 失败

**设计**:
- 负载: 循环交替 VE DGEMM + Phi FMA (每 5s 一批)
- 监控: veda-smi 温度/功耗每 30s 采集一次
- 记录: 温度趋势、时钟频率、失败次数
- 报告: 统计图表

**预估**:
- VE 满载时温度应 ≤70°C (当前 idle 40°C，delta ~30°C)
- Phi 被动散热可能上升到 70-80°C (已知风险)

---

## 3. 文件布局

```
uni/
├── src/kernels/
│   ├── ve/
│   │   ├── pcie_bw.c          # PCIe 带宽测试 (新增)
│   │   ├── pcie_bw_ve         # 编译产物
│   │   ├── mpi_allreduce.c    # MPI AllReduce (新增)
│   │   └── mpi_allreduce_ve   # 编译产物
│   └── phi/
│       └── pcie_bw.c          # Phi PCIe 带宽测试 (新增)
├── scripts/
│   ├── bench_all.py           # 已有 — 扩展覆盖 TC-002/003
│   ├── bench_pcie.py          # TC-001 (新增)
│   ├── bench_mpi.py           # TC-004 (新增)
│   ├── bench_power.py         # TC-005 (新增)
│   └── bench_stability.py     # TC-006 (新增)
├── src/benchmarks/            # Phase 1 规划的基准测试包
│   ├── __init__.py
│   ├── pcie_bw_stress.py      # TC-001 封装
│   ├── multi_device_throughput.py  # TC-002 封装
│   └── pipeline_latency.py    # TC-003 封装
└── docs/
    ├── plan/20260603_phase3_bench_plan.md     # 本文件
    └── impl/                     # 实现记录 (待迭代)
```

---

## 4. 实施顺序

| 步骤 | 内容 | 预估耗时 | 依赖 |
|------|------|---------|------|
| 1 | 写 VE/Phi PCIe 带宽内核 + bench_pcie.py | 30min | — |
| 2 | TC-001 运行 + 结果记录 | 15min | 步骤1 |
| 3 | 扩展 bench_all.py 覆盖 TC-002 (N=2048 NLC) | 20min | — |
| 4 | TC-002 运行 + 结果记录 | 15min | 步骤3 |
| 5 | 写基准脚本 bench_pipeline_latency.py | 20min | — |
| 6 | TC-003 运行 + 结果记录 | 15min | 步骤5 |
| 7 | 写 MPI AllReduce 内核 + bench_mpi.py | 45min | — |
| 8 | TC-004 运行 + 结果记录 | 15min | 步骤7 |
| 9 | 写 bench_power.py (veda-smi + RAPL 集成) | 30min | — |
| 10 | TC-005 运行 + 结果记录 | 20min | 步骤9 |
| 11 | 写 bench_stability.py | 20min | 步骤9 |
| 12 | TC-006 运行 (30min) + 结果记录 | 35min | 步骤11 |

**总计: ~5h** (含等待时间)

---

## 5. 风险

| 风险 | 概率 | 缓解 |
|------|------|------|
| Phi SSH 不可用 (无法读温) | 确定 | 用 micnativeloadex 执行温度读取内核 |
| VE2 fw 5127 MPI 兼容性 | 低 | 先单卡 → 双卡 → 三卡逐步测试 |
| 满载超过 1600W | 中 | PowerCap 已有 1440W 安全预算 |
| Phi PCIe 带宽测量精度低 | 中 | 用大 buffer (256MB+) 摊还启动开销 |
| veda-smi 在满载时采集延迟 | 低 | 采集间隔 5-10s，异步采集 |
