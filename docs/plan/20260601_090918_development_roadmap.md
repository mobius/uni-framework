# Intel Phi 7120P + NEC VE 1.0×3 异构计算开发路线图

> 规划日期: 2026-06-01
> 目标: 在 ESC4000 G4 上实现 Phi + VE 异构协同计算
> 阅读前置: `uni/docs/research/20260601_090918_heterogeneous_system_analysis.md`

---

## 概述

本路线图将开发分为 4 个阶段 (Phase 0-4)，总计约 6 周。核心策略：

1. **先验证硬件，再写代码** — Phase 0 确认 PSU/散热/内存实际状态
2. **uv 优先，不污染全局** — 所有 Python 依赖通过 uv 管理
3. **独立编译 + Python 调度** — Phi 和 VE 各自独立编译，Host 侧 Python 统一调度
4. **PCIe 最小化** — 所有算法设计以最小化 Host↔Device 数据搬运为前提

---

## Phase 0: 环境验证与基线确认 (预计 2-3 天)

### 目标

确认当前硬件环境的实际状态，验证已有文档中的数据，补充缺失信息。

### 检查清单

```bash
# 0.1 确认电源实际型号与功率
ipmitool fru print  # 或通过 BMC Web 界面

# 0.2 确认主机实际内存
free -h
dmidecode -t memory | grep -E "Size|Type|Speed"

# 0.3 确认 NUMA 拓扑
numactl --hardware
lscpu | grep -E "NUMA|Socket"

# 0.4 确认 VE 三卡状态
sudo /opt/nec/ve/bin/vecmd state get
for i in 0 1 2; do
  echo "=== VE$i ==="
  cat /sys/class/ve/ve$i/fw_version 2>/dev/null
  cat /sys/class/ve/ve$i/numa_node 2>/dev/null
done

# 0.5 确认 Phi 状态
systemctl status mpss
micctrl --status
micinfo | grep -E "Device|Cores|Threads|Memory"

# 0.6 确认 PCIe 拓扑 (每张卡的物理 Slot)
lspci -t -v | grep -A2 -E "NEC|Phi"
# 对照 ESC4000G4 物理布局

# 0.7 Phi 满载 10min 温度测试
# 编译并运行 phi_peak_fp64.mic，监控温度
ssh mic0 cat /sys/class/thermal/thermal_zone0/temp

# 0.8 VE 三卡并发满载功耗测试
# 同时运行三卡 ve_matmul，监控功耗
```

### 通过标准

| 项目 | 通过条件 | 不通过时的处理 |
|------|---------|--------------|
| PSU 功率 | ≥2000W (理想) / ≥1600W (可用) | 1600W: 加功率封顶层 |
| 内存 | ≥128GB | <128GB: 流式处理, 或升级 |
| Phi 温度 | 10min 满载 <85°C | ≥85°C: Phi 仅短时任务 |
| VE 三卡 ONLINE | 3/3 OK | 排查 VEOS 服务 |
| VE2 固件 | ≥5400 | 5127: 安排升级 |

---

## Phase 1: 统一软件栈搭建 (预计 3-5 天)

### 1.1 目录结构

```
uni/
├── README.md
├── docs/
│   ├── research/       # 调研文档
│   ├── plan/           # 规划文档
│   └── impl/           # 实现记录 (每次迭代以时间戳命名)
├── env/
│   ├── pyproject.toml   # uv 项目定义 (Python ≥3.10)
│   ├── .venv/           # uv 虚拟环境
│   └── requirements.txt
├── src/
│   ├── scheduler/       # 统一调度层
│   │   ├── __init__.py
│   │   ├── devices.py    # Device enum: PhiDevice, VEDevice
│   │   ├── phi.py        # Phi 编译/部署/执行管理
│   │   ├── ve.py         # VE 编译/部署/执行管理
│   │   ├── numa.py       # NUMA 亲和绑定封装
│   │   ├── power.py      # 功耗监控与封顶
│   │   └── task_graph.py # DAG 任务依赖图调度
│   ├── kernels/          # 计算内核
│   │   ├── phi/          # Phi 端 (C, ICC 编译 → .mic)
│   │   │   ├── dgemm_blocked.c
│   │   │   ├── stream_bench.c
│   │   │   ├── pagerank_kernel.c
│   │   │   └── Makefile
│   │   └── ve/           # VE 端 (C+Fortran, ncc/nfort 编译)
│   │       ├── dgemm_kernel.f90
│   │       ├── matvec.c
│   │       ├── bandwidth.c
│   │       └── Makefile
│   ├── benchmarks/       # 协同基准测试
│   │   ├── pcie_bw_stress.py
│   │   ├── multi_device_throughput.py
│   │   └── pipeline_latency.py
│   └── apps/             # 示例应用
│       ├── hetero_dgemm/  # 异构矩阵乘法
│       ├── hetero_pagerank/ # 异构 PageRank
│       └── pipeline_template/ # 可复用流水线模板
├── scripts/
│   ├── check_hw.sh       # Phase 0 硬件检查
│   ├── setup_env.sh      # 一键环境初始化
│   └── run_bench.sh      # 基准测试入口
└── tests/
    └── test_scheduler.py
```

### 1.2 软件栈选择

| 层 | 工具 | 安装方式 | 备注 |
|----|------|---------|------|
| Python 运行时 | Python 3.10+ | 系统已有 | Rocky 8.10 自带 |
| 包管理 | uv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | 已安装 |
| 调度框架 | Python stdlib (asyncio) | — | 无第三方依赖 |
| 进度显示 | rich | `uv pip install rich` | 终端美化 |
| 数据处理 | numpy | `uv pip install numpy` | 数组操作 |
| Phi 编译 | ICC 16.0 in podman | 已有容器 `centos7-phi-dev` | 不污染 host |
| VE 编译 | ncc/nfort 5.4.1 | 已 RPM 安装 | — |
| VE 库 | NLC 3.1.0 | 已安装 | `cblas_dgemm` |
| VE MPI | NEC MPI 3.10.0 | 已安装 | 跨卡通信 |

### 1.3 pyproject.toml 骨架

```toml
[project]
name = "uni-scheduler"
version = "0.1.0"
description = "Heterogeneous compute scheduler for Intel Phi 7120P + NEC VE 1.0"
requires-python = ">=3.10"
dependencies = [
    "numpy>=1.24",
    "rich>=13.0",
]

[project.scripts]
uni-bench = "benchmarks.run_all:main"
uni-sched = "scheduler.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### 1.4 关键环境变量

```bash
# VE 相关
export PATH=/opt/nec/ve/bin:$PATH
export VE_LD_LIBRARY_PATH=/opt/nec/ve/nfort/5.4.1/lib:/opt/nec/ve/ncc/5.4.1/lib
source /opt/nec/ve/mpi/3.10.0/bin64/necmpivars-runtime.sh  # MPI

# Phi (OpenMP offload)
export MIC_LD_LIBRARY_PATH="/opt/intel/compilers_and_libraries_2016.0.109/linux/compiler/lib/intel64_lin_mic"
export OFFLOAD_ENABLE_ORSL=0
```

---

## Phase 2: 核心调度层开发 (预计 1-2 周)

### 2.1 调度层架构

```
                    ┌──────────────────────────┐
                    │       TaskGraph           │  DAG 任务图
                    │  (task_graph.py)          │  拓扑排序 + 并行执行
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
    │                     Host Layer                       │
    │  ┌─────────────┐  ┌──────────────┐  ┌────────────┐ │
    │  │  PhiRunner   │  │  VERunner    │  │  MPIRunner  │ │
    │  │  (ssh/load)  │  │  (ve_exec/   │  │  (mpirun    │ │
    │  │              │  │   AVEO)      │  │   -ve 0-2)  │ │
    │  └──────┬───────┘  └──────┬───────┘  └──────┬─────┘ │
    │         │                 │                  │       │
    └─────────┼─────────────────┼──────────────────┼───────┘
              │ PCIe            │ PCIe             │ PCIe
         ┌────┴────┐      ┌─────┴─────┐      ┌─────┴─────┐
         │ Phi     │      │ VE0 VE1   │      │ VE0↔VE1↔VE2│
         │ 7120P   │      │ VE2       │      │  MPI ring  │
         └─────────┘      └───────────┘      └───────────┘
```

### 2.2 核心接口

```python
# src/scheduler/devices.py
@dataclass
class DeviceInfo:
    name: str           # "phi0", "ve0", "ve1", "ve2"
    kind: str           # "phi" | "ve"
    online: bool
    numa_node: int
    memory_gb: float
    pcie_addr: str
    temperature_c: float
    power_draw_w: float

class DeviceManager:
    def discover() -> list[DeviceInfo]
    def health_check(device: str) -> bool
    def get_temperature(device: str) -> float

# src/scheduler/task_graph.py
@dataclass
class Task:
    name: str
    device: str         # "phi0" | "ve0" | "ve1" | "ve2"
    kernel_path: str    # 可执行文件路径
    inputs: list[str]   # 输入文件/数据路径
    outputs: list[str]  # 输出文件/数据路径
    estimated_gflops: float
    estimated_watts: float

class TaskGraph:
    def add_task(task: Task, depends_on: list[str] = [])
    async def execute() -> dict[str, TaskResult]
    def estimated_total_power() -> float
```

### 2.3 关键实现要点

1. **Phi 执行**: 
   - MIC Native 模式: `micnativeloadex kernel.mic -d 0 -t 60`
   - OpenMP offload 模式: 直接运行 host binary (已内嵌 offload pragma)
   - 编译: `podman exec centos7-phi-dev icc -mmic -O3 -openmp ...`

2. **VE 执行**:
   - Native: `ve_exec -N <id> ./kernel`
   - AVEO: 通过 `libveo` 创建 proc context
   - 多卡: 独立 ve_exec 并行, 或 `mpirun -ve 0-2 -np 3`

3. **NUMA 绑定**:
   - `numactl --cpunodebind=$N --membind=$N ve_exec -N $N ./kernel`
   - VE0 → NUMA0, VE1/VE2 → NUMA1 (根据实际拓扑)
   - Phi → 根据物理 Slot 对应的 NUMA node

4. **功率封顶**:
   - 实时读取 sysfs sensor 或 veda-smi
   - 新任务启动前检查 `estimated_total_power() < 1600W`
   - 超过阈值: 排队等待

---

## Phase 3: 协同基准测试套件 (预计 1 周)

### 测试案例

| 编号 | 测试项 | 模式 | 指标 |
|------|-------|------|------|
| TC-HETERO-001 | PCIe 带宽压力 | 同时 4 卡数据传输 | H2D/D2H 总吞吐, 争抢度 |
| TC-HETERO-002 | 数据中心吞吐 | 4 卡独立 DGEMM | GFLOPS 合计, 线性度 |
| TC-HETERO-003 | 流水线延迟 | Phi→VE 数据中转 | 端到端延迟, PCIe 中转开销 |
| TC-HETERO-004 | VE-MPI 扩展性 | 3 卡 AllReduce | 单/双/三卡扩展效率 |
| TC-HETERO-005 | 功率封顶验证 | 逐步加载至 1600W | 封顶策略有效性 |
| TC-HETERO-006 | 30min 稳定性 | 混合负载持续运行 | 温度趋势, 无降频, 0 失败 |

### 通过标准

| 测试 | 通过条件 |
|------|---------|
| TC-HETERO-001 | H2D 总吞吐 ≥ 30 GB/s (4 卡合计) |
| TC-HETERO-002 | FP64 总算力 ≥ 5.0 TFLOPS (Phi+3VE) |
| TC-HETERO-003 | 流水线延迟 overhead ≤ 20% vs 纯 VE |
| TC-HETERO-004 | 三卡扩展效率 ≥ 95% |
| TC-HETERO-005 | 功率封顶生效, 无 OTP 触发 |
| TC-HETERO-006 | 30min 无失败, 温度稳定 |

---

## Phase 4: 示例应用开发 (预计 2-3 周)

### 4.1 异构 DGEMM

- 模式: A (数据中心型)
- 分工: VE×3 做 90% 矩阵计算 (NLC), Phi 做 10% 边界块 (MKL or 手动)
- 目标: 证明多卡并发调度无串扰, NUMA 绑定正确

### 4.2 异构 PageRank

- 模式: B (流水线型)
- 分工: Phi 做图遍历 (edge loading), VE×3 做矩阵向量乘
- 目标: 证明 PCIe 中转可行, 端到端延迟可接受

### 4.3 混合流水线模板

- 模式: A+B 可配置
- 目标: 可复用的 pipeline 框架，用户只需定义 Task DAG

---

## 时间线

```
Week 1:  ████████  Phase 0 硬件验证 + Phase 1 软件栈搭建
Week 2:  ████████  Phase 2 调度层核心开发
Week 3:  ████████  Phase 2 调度层完成 + Phase 3 基准测试
Week 4:  ████████  Phase 4 示例应用: 异构 DGEMM
Week 5:  ████████  Phase 4 示例应用: PageRank + Pipeline 模板
Week 6:  ████████  文档完善 + 性能报告
```

---

## 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 1600W PSU 不满足同时满载 | 确定 | 高 | Phase 0 首先确认; 功率封顶层 |
| Phi 被动散热不足 | 中 | 中 | Slot1 强制; 温度监控; 短时任务 |
| 主机内存 <128GB | 高 | 高 | 流式处理; 按需 DMA; 或升级 |
| PCIe 带宽争抢 | 确定 | 中 | TaskGraph 层做 PCIe 流量仲裁 |
| VE2 固件版本不一致 | 低 | 低 | 实测无影响; 建议统一升级 |
| ICC 16.0 容器维护 | 低 | 中 | 已有固化容器方案 |

---

## 附: Phase 0 启动命令

```bash
# 一键硬件检查
cd /home/joey/Work/uni
mkdir -p docs/{research,plan,impl}
bash scripts/check_hw.sh 2>&1 | tee docs/research/$(date +%Y%m%d_%H%M%S)_hw_check.log

# 初始化 Python 环境
cd env
uv venv
source .venv/bin/activate
uv pip install -e .
```
