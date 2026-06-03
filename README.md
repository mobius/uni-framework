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

## 目录结构

```
uni/
├── README.md                           # 本文件
├── docs/
│   ├── research/                       # 调研文档 (时间戳命名)
│   ├── plan/                           # 规划文档 (时间戳命名)
│   └── impl/                           # 实现记录 (每次迭代)
├── env/                                # Python 环境 (uv 管理)
├── src/
│   ├── scheduler/                      # 统一调度层
│   ├── kernels/{phi,ve}/               # 计算内核
│   ├── benchmarks/                     # 协同基准测试
│   └── apps/                           # 示例应用
├── scripts/                            # 辅助脚本
└── tests/                              # 单元测试
```

## 文档索引

| 文档 | 路径 | 内容 |
|------|------|------|
| 系统全景分析 | `docs/research/20260601_090918_heterogeneous_system_analysis.md` | 硬件规格、瓶颈识别、编程模型、协同模式 |
| 开发路线图 | `docs/plan/20260601_090918_development_roadmap.md` | Phase 0-4 分阶段规划、时间线、风险 |

## 快速开始

```bash
# 1. 硬件检查
bash scripts/check_hw.sh

# 2. 初始化 Python 环境 (uv, 不污染全局)
cd env
uv venv
source .venv/bin/activate
uv pip install numpy rich

# 3. 运行示例
cd ..
bash examples/basic/run.sh      # 四卡并行基线验证
bash examples/multi_task/run.sh # 多卡 DAG 任务流
```

## 示例

| 示例 | 路径 | 说明 |
|------|------|------|
| Basic | `examples/basic/` | 4 卡独立 FP64 峰值验证 |
| Multi-Task | `examples/multi_task/` | 6 任务 DAG 异构流 |

## 关键约束

- **PCIe Gen3 ×16**: 加速器内带宽 4.4 TB/s，PCIe 仅 15.75 GB/s，比值约 280:1
- **PSU 1600W**: 满载 1730W 超过额定，不可同时满载
- **Phi 被动散热**: 必须放在 Slot 1 (最靠近进风口)
- **编程模型不兼容**: ICC 16.0 vs ncc 5.4.1，无统一编程框架

## 核心策略

1. PCIe 最小化 — 数据加载后在卡内闭环计算
2. 任务特征匹配 — 稠密计算 VE，不规则访问 Phi
3. Python 调度层 — asyncio DAG 任务图 + NUMA 亲和 + 功率封顶
4. uv 优先 — 不污染全局 Python 环境
