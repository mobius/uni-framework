# Phase 2 收尾 — NUMA 绑定 + 功率封顶

> 日期: 2026-06-03
> 规划: `docs/plan/20260601_090918_development_roadmap.md`
> 前置: V1-V4 历次迭代
> 状态: Phase 2 (核心调度层) 全部完成

---

## 1. 收尾内容

Phase 2 路线图要求 6 个调度模块，V1-V4 已完成其中 4 个（devices / phi / ve / task_graph），本次补全剩余 2 个：

| 模块 | 状态 | 文件 |
|------|------|------|
| devices | ✅ V1 | `src/scheduler/devices.py` |
| phi | ✅ V1 | `src/scheduler/phi.py` |
| ve | ✅ V1 | `src/scheduler/ve.py` |
| task_graph | ✅ V2 | `src/scheduler/task_graph.py` |
| profiler | ✅ V3 | `src/scheduler/profiler.py` |
| **numa** | ✅ 本次 | `src/scheduler/numa.py` (新增) |
| **power** | ✅ 本次 | `src/scheduler/power.py` (新增) |

---

## 2. numa.py — NUMA 亲和绑定

### 设计

- `NUMABinder` 类：封装 numactl 绑定逻辑
- 拓扑自动发现：调用 `devices.discover_all()` 读取各卡的 sysfs NUMA node
- 默认回退：ES4000 G4 已验证拓扑 — VE0→NUMA0, VE1/VE2→NUMA1, Phi→NUMA0
- 降级：numactl 不可用时返回空串，不影响执行

### 集成

- `ve.py` `run_ve_kernel()`: 新增 `auto_numa=True`，自动从 `NUMABinder` 获取最优绑定
- `phi.py` `run_phi_kernel()`: 新增 `auto_numa=True`，自动绑定

### 用法

```python
from scheduler.numa import get_binder
binder = get_binder()
prefix = binder.bind_cmd("ve1")  # → "numactl --cpunodebind=0 --membind=0 "
```

---

## 3. power.py — 功耗监控与封顶

### 预估模型

基于 TDP 文档和 V1-V4 实测校准：

| 设备 | FMA峰值 | DGEMM | 轻量 ops | 空闲 |
|------|--------|-------|----------|------|
| Phi 7120P | 280W | 280W | 150W | 50W |
| VE 1.0 | 280W | 280W | 150W | 50W |
| Host | 300W | 200W | 100W | 50W |

有效预算：1600W × 90% = **1440W**

最坏情况满载：Phi (280) + 3×VE (840) + Host (300) = 1420W < 1440W ✅，但已逼近边界。

### 实时功耗读取

- `ipmitool sensor` — 系统总功耗（最准确，需 BMC 支持）
- Intel RAPL (`/sys/class/powercap/`) — CPU package 功耗
- 两者不可用时回退到预估模型

### PowerCap 类

- `can_launch(devices, ops)` — 启动前检查预算
- `reserve(devices, ops)` / `release(devices)` — 预留/释放
- 有效预算 1440W，超过则任务排队

### 集成

- `task_graph.py` `TaskNode`: 新增 `op` 和 `estimated_watts` 字段
- `task_graph.py` `TaskGraph`: 新增 `estimated_total_power()`, 可选 `power_cap` 参数
- `execute()`: 启动 ready 任务前检查功率预算，超额时打印 `⏸` 等待标记

### 用法

```python
from scheduler.power import PowerCap
from scheduler.task_graph import TaskGraph, TaskNode

cap = PowerCap(psu_limit_w=1600)
graph = TaskGraph(power_cap=cap)

node = TaskNode("matmul", "ve1", fn, op="dgemm", estimated_watts=280)
graph.add(node)
results = await graph.execute()
# 功率预算不足时会自动排队
```

---

## 4. 修改文件清单

```
uni/
├── src/scheduler/
│   ├── __init__.py          # 导出 numa + power 模块 (更新)
│   ├── numa.py              # NUMA 亲和绑定 (新增)
│   ├── power.py             # 功耗监控与封顶 (新增)
│   ├── ve.py                # run_ve_kernel 集成 auto_numa (修改)
│   ├── phi.py               # run_phi_kernel 集成 auto_numa (修改)
│   └── task_graph.py        # TaskNode +estimated_watts, PowerCap 集成 (修改)
└── docs/impl/
    └── 20260603_phase2_close.md  # 本文件 (新增)
```

---

## 5. Phase 2 完成判定

对照路线图 Phase 2 需求：

| 需求 | 状态 |
|------|------|
| DeviceManager 设备发现 + 健康检查 | ✅ devices.py |
| PhiRunner (ssh/load) | ✅ phi.py |
| VERunner (ve_exec) | ✅ ve.py |
| NUMABinder (numactl 自动绑定) | ✅ numa.py (本次) |
| PowerCap (功率安全网) | ✅ power.py (本次) |
| TaskGraph DAG 调度 (拓扑排序+并行) | ✅ task_graph.py |
| Profiler (预估+实测对比) | ✅ profiler.py |

**Phase 2: ✅ 完成**。下一步进入 Phase 3（协同基准测试套件）和 Phase 4（示例应用开发）。

---

## 6. 已知局限

- `PowerCap` 的实时功耗读取（ipmitool/RAPL）在开发机上不可用，需要在 ESC4000 G4 上验证
- `NUMABinder` 的拓扑自动发现依赖 `devices.discover_all()`，在无硬件环境下回退到硬编码映射
- `task_graph.py` 的功率排队机制是软性的（下次循环重试），没有实现优先级队列
- 当前 `bench_all.py` 未使用 `PowerCap` 和 `NUMABinder`，保持向后兼容
