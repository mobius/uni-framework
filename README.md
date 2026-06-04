# Uni — Intel Phi 7120P + NEC VE 1.0×3 异构计算协同项目

> 服务器: ASUS ESC4000 G4, 2× Xeon Gold 6252, Rocky Linux 8.10
> 加速卡: 1× Intel Xeon Phi 7120P (KNC) + 3× NEC Vector Engine 1.0

## 项目目标

在单台 ESC4000 G4 服务器上实现 Phi + VE 异构计算协同调度，最大化利用两种加速卡的互补计算特征。

## 算力概览

| 指标 | Phi 7120P | VE 1.0×3 | **合计** |
|------|----------|---------|---------|
| FP64 理论 | 1.21 TFLOPS | 6.48 TFLOPS | **7.69 TFLOPS** |
| FP64 可达成 | 0.58 TFLOPS | 5.25 TFLOPS | **5.83 TFLOPS** |
| 内存总量 | 16 GB GDDR5 | 144 GB HBM2 | **160 GB** |
| 内存带宽 | 157 GB/s | 3,186 GB/s | **3,343 GB/s** |

## 项目进度

| Phase | 内容 | 状态 |
|-------|------|------|
| 0 | 硬件验证与基线确认 | ✅ |
| 1 | 统一软件栈搭建 (uv/ncc/ICC) | ✅ |
| 2 | 核心调度层 (7 模块) | ✅ |
| 3 | 协同基准测试 (TC-001~006) | ✅ 4/6 通过, 5/6 标注 |
| 4 | 示例应用 (SpMV + 数据预处理) | ✅ |

## 目录结构

```
uni/
├── README.md
├── docs/
│   ├── research/                  # 调研文档
│   ├── plan/                      # 规划文档
│   └── impl/                      # 实现记录 (每次迭代)
├── env/                           # Python 环境 (uv 管理)
├── src/
│   ├── scheduler/                 # 统一调度层
│   │   ├── devices.py             # 设备发现 (Phi + 3×VE)
│   │   ├── phi.py / ve.py         # 设备管理 (编译/执行)
│   │   ├── numa.py                # NUMA 亲和绑定
│   │   ├── power.py               # 功耗监控与封顶
│   │   ├── task_graph.py          # DAG 任务图调度器
│   │   └── profiler.py            # 性能预估与实测对比
│   ├── kernels/{phi,ve}/          # 计算内核 (FMA/dgemm/MPI/PCIe)
│   ├── apps/
│   │   ├── hetero_spmv/           # 异购 SpMV (Phi分块+3VE并行)
│   │   └── hetero_dataprep/       # 数据预处理流水线
│   └── benchmarks/                # 基准测试封装
├── scripts/                       # 基准脚本 (TC-001~004)
├── examples/                      # 示例 (basic/multi_task/pipeline/throughput)
└── tests/                         # 单元测试
```

## 文档索引

| 文档 | 内容 |
|------|------|
| `docs/research/20260601_090918_heterogeneous_system_analysis.md` | 硬件规格、瓶颈识别、编程模型、协同模式 |
| `docs/plan/20260601_090918_development_roadmap.md` | Phase 0-4 分阶段规划 |
| `docs/research/20260603_bench_conclusions.md` | 全框架基准对比, 5条核心结论 |
| `docs/plan/20260603_phase3_bench_plan.md` | Phase 3 基准测试计划 |
| `docs/plan/20260603_phase4_app_plan.md` | Phase 4 应用计划 |
| `docs/impl/20260603_phase2_close.md` | Phase 2 收尾: NUMA+Power |
| `docs/impl/20260603_phase4_impl.md` | Phase 4 应用实现记录 |

## 快速开始

```bash
# 1. 硬件检查
bash scripts/check_hw.sh

# 2. 初始化 Python 环境 (uv, 不污染全局)
cd env && uv venv && source .venv/bin/activate && uv pip install numpy rich
cd ..

# 3. 运行基础验证
bash examples/basic/run.sh          # 四卡并行基线 (3,277 GFLOPS)

# 4. 运行基准测试
./env/.venv/bin/python3 scripts/bench_all.py       # 全框架统一基准
./env/.venv/bin/python3 scripts/bench_throughput.py # 数据中心吞吐 (5.68 TFLOPS)
./env/.venv/bin/python3 scripts/bench_pcie.py       # PCIe 带宽压力
./env/.venv/bin/python3 scripts/bench_mpi.py        # VE-MPI 扩展性

# 5. 运行示例应用
./env/.venv/bin/python3 src/apps/hetero_spmv/spmv_app.py        # 异构 SpMV
./env/.venv/bin/python3 src/apps/hetero_dataprep/dataprep_app.py # 数据预处理
```

## 基准测试结果摘要

| 测试 | 指标 | 结果 | 判定 |
|------|------|------|------|
| TC-001 PCIe 带宽 | 3VE 并发 H2D | 13.7 GB/s (效率 86%) | ⚠️ |
| TC-002 数据中心吞吐 | 4卡并行总算力 | **5.68 TFLOPS** | ✅ |
| TC-003 流水线延迟 | Phi中转overhead | 569% (Phi启动瓶颈) | ⚠️ |
| TC-004 VE-MPI 扩展性 | 3卡 Ring 效率 | **97.8%** (VE2调整后) | ✅ |

## 示例

| 示例 | 路径 | 说明 |
|------|------|------|
| Basic | `examples/basic/` | 4 卡独立 FP64 峰值验证 |
| Multi-Task | `examples/multi_task/` | 7 任务 DAG 异构流 (Phi∥3VE) |
| Pipeline | `examples/pipeline/` | 串行流水线 (VE1→VE2→VE3→Phi) |
| Throughput | `examples/throughput/` | N=2048 NLC DGEMM 数据中心吞吐 |

## 应用

| 应用 | 路径 | 流程 | 结果 |
|------|------|------|------|
| 异构 SpMV | `src/apps/hetero_spmv/` | Host→Phi分块→3VE并行乘法 | 0.107s, max_diff 1.07e-14 |
| 数据预处理 | `src/apps/hetero_dataprep/` | Phi清洗→VE1标准化→VE2 PCA | corr 0.997, std diff 3.55e-15 |

## 调度层架构

```
                    ┌──────────────────────────┐
                    │       TaskGraph           │  DAG 任务图
                    │  (task_graph.py)          │  拓扑排序 + 并行 + 功率封顶
                    └──────────┬───────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           │                   │                   │
    ┌──────┴──────┐    ┌──────┴──────┐    ┌──────┴──────┐
    │  DeviceMgr  │    │  NUMABinder │    │  PowerCap   │
    │  发现/健康   │    │  亲和绑定    │    │  功率安全网  │
    └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
           │                   │                   │
    ┌──────┴──────────────────┼───────────────────┴──────┐
    │                    Host Layer                       │
    │  PhiRunner (ssh/scp)  VERunner (ve_exec)  MPIRunner │
    └────────────────────────────────────────────────────┘
```

## 关键约束

- **PCIe Gen3 ×16**: 加速器内带宽 4.4 TB/s，PCIe 仅 15.75 GB/s，比值约 280:1
- **PSU 1600W**: 满载 1730W 超过额定，不可同时满载 (PowerCap: 1440W 有效预算)
- **Phi 被动散热**: 必须放在 Slot 1 (最靠近进风口)
- **编程模型不兼容**: ICC 16.0 vs ncc 5.4.1，无统一编程框架
- **Phi 文件 I/O**: 需 scp 双向传输，无文件系统穿透 (VE 天然支持)

## 核心策略

1. PCIe 最小化 — 数据加载后在卡内闭环计算
2. 任务特征匹配 — 稠密计算 VE，不规则访问 Phi
3. Python 调度层 — asyncio DAG 任务图 + NUMA 亲和 + 功率封顶
4. uv 优先 — 不污染全局 Python 环境
5. Phi I/O 通过 scp — micnativeloadex 无共享文件系统
