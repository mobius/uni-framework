# Intel Xeon Phi 7120P + NEC VE 1.0×3 异构计算系统全景分析

> 调研日期: 2026-06-01
> 服务器: ASUS ESC4000 G4 / Z11PG-D16
> CPU: 2× Intel Xeon Gold 6252 (48C/96T), Rocky Linux 8.10
> 加速卡: 1× Intel Xeon Phi 7120P (KNC) + 3× NEC Vector Engine 1.0

---

## 1. 硬件规格汇总

### 1.1 Intel Xeon Phi 7120P (Knights Corner)

| 规格 | 数值 | 来源 |
|------|------|------|
| 代际 | Knights Corner (KNC, 第一代) | 厂商文档 |
| 核心 | 61 x86 核 @ 1.238 GHz | micinfo |
| 线程 | 244 (4-way SMT) | micinfo |
| L2 缓存 | 30.5 MB (512 KB/核) | 规格书 |
| 内存 | 16 GB GDDR5 | micinfo |
| 内存带宽 (理论) | 352 GB/s | 规格书 |
| 内存带宽 (实测) | 157 GB/s (COPY, 44.7%) | phi_stream_bench.mic |
| PCIe | Gen3 ×16 | lspci |
| FP64 理论峰值 | 1.208 TFLOPS | 61×1.238GHz×16 FMA |
| FP64 实测峰值 | 575 GFLOPS (47.6%) | phi_peak_fp64.mic |
| FP32 理论峰值 | 2.416 TFLOPS | 61×1.238GHz×32 FMA |
| FP32 实测峰值 | 1,170 GFLOPS (48.4%) | phi_peak_fp32.mic |
| DGEMM 2048 (实测) | 63 GFLOPS (naive), 46 GFLOPS (MKL) | phi_peak_dgemm.mic |
| TDP | **300W** | 规格书 |
| 散热方式 | **被动散热** (无自带风扇) | 7120**P** = Passive |
| 卡内系统 | Linux 2.6.38.8+mpss3.8.6 (BusyBox) | ssh mic0 |
| 编译器 | ICC 16.0 (podman 容器) | 已验证 |
| MPSS 版本 | 3.8.6 | 已验证 |

### 1.2 NEC Vector Engine 1.0 (10BE-P / 10B-P, 三卡)

| 规格 | 单卡数值 | 三卡合计 | 来源 |
|------|---------|---------|------|
| 向量核心 | 8 @ ~1.4 GHz | 24 | sysfs /proc/ve |
| L2 缓存/核 | 256 KB | — | sysfs |
| LLC (共享) | 16 MB | 48 MB | sysfs |
| 内存 | 48 GB HBM2 | 144 GB | veda-smi |
| HBM2 带宽 (理论) | 1,350 GB/s | 4,050 GB/s | 厂商规格 |
| HBM2 带宽 (实测) | 1,062 GB/s (78.7%) | 3,186 GB/s | ve_bandwidth |
| PCIe | Gen3 ×16 | 3× Gen3 ×16 | lspci |
| FP64 理论峰值 | 2.16 TFLOPS | 6.48 TFLOPS | 厂商规格 |
| FP64 NLC DGEMM (实测) | ~1,750 GFLOPS (81%) | ~5,250 GFLOPS | cblas_dgemm |
| FP64 nfort DGEMM (实测) | ~434 GFLOPS (20%) | ~1,302 GFLOPS | ve_matmul |
| FP64 naive (实测) | 54 GFLOPS (2.5%) | 162 GFLOPS | fulltest 单线程 |
| MPI Ping-Pong 延迟 | ~1.52 μs | — | mpi_pingpong |
| PCIe H2D (实测) | ~10 GB/s | — | aveo_bandwidth |
| PCIe D2H (实测) | ~5.3 GB/s | — | aveo_bandwidth |
| 满载功耗 (实测) | ~99 W | ~292 W | sysfs sensor |
| 满载温度 (实测) | ~64°C | — | veda-smi |
| TDP | ~300W | ~900W | 规格书 (厂商未公开精确值) |
| 散热方式 | **主动散热** (涡轮风扇) | — | 硬件观察 |
| 编译器 | ncc/nc++/nfort 5.4.1 | — | RPM 包 |
| MPI | NEC MPI 3.10.0 | — | RPM 包 |
| BLAS | NEC NLC 3.1.0 | — | RPM 包 |
| VEOS | 3.6.1 | — | 系统安装 |
| 固件 | VE1=5400, VE2=5127, VE3=5400 | — | vecmd fwup check |

### 1.3 主机系统

| 规格 | 数值 |
|------|------|
| CPU | 2× Intel Xeon Gold 6252 (Cascade Lake, 24C/48T per socket) |
| NUMA | 2 nodes |
| 内存 | 62-64 GB DDR4 (需确认：文档中另有 192GB 描述) |
| OS | Rocky Linux 8.10, kernel 4.18.0-553 |
| PSU | 1+1 冗余 1600W 80Plus Platinum (需实地确认实际功率) |
| 机箱 | 2U, 4-6 双宽 PCIe 插槽 |

---

## 2. 系统总算力与瓶颈

### 2.1 算力全景

| 指标 | Phi 贡献 | VE×3 贡献 | **合计** | Phi 占比 |
|------|---------|----------|---------|----------|
| FP64 理论 | 1.21 TFLOPS | 6.48 TFLOPS | **7.69 TFLOPS** | 15.7% |
| FP64 可达成 | 0.58 TFLOPS | 5.25 TFLOPS | **5.83 TFLOPS** | 9.9% |
| 内存总量 | 16 GB | 144 GB | **160 GB** | 10.0% |
| 内存带宽 | 157 GB/s | 3,186 GB/s | **3,343 GB/s** | 4.7% |

**结论**: 3 张 VE 贡献了约 90% 的算力和 95% 的带宽。Phi 在算力维度是明显配角。

### 2.2 六大瓶颈排序

| 优先级 | 瓶颈 | 严重程度 | 缓解方式 |
|--------|------|---------|---------|
| P0 | **PCIe Gen3 ×16 带宽** | 致命 (15.75 GB/s vs 加速器侧 4.4 TB/s) | 数据预加载, 卡内闭环计算, 流式 DMA |
| P0 | **主机内存 64GB** | 致命 (3VE+Phi 总 HBM 160GB) | 升级至 256GB, 或流式处理 |
| P1 | **PSU 1600W** | 高 (满载 1730W > 1600W) | 功率封顶, 避免同时满载 |
| P1 | **Phi 被动散热** | 高 (300W 无风扇, 必须 Slot1) | 强制 Slot1 位置, 环境温度控制 |
| P1 | **编程模型鸿沟** | 高 (ICC vs ncc, 无统一框架) | Python 调度层, 独立编译链 |
| P2 | **NUMA 跨节点访问** | 中 (30-50% 性能损失) | numactl 绑定, 调度层自动分配 |

---

## 3. 编程模型对比

### 3.1 Intel Xeon Phi 7120P 可用模型

| 模型 | 语法 | 编译 | 性能级别 | 复杂度 |
|------|------|------|---------|--------|
| OpenMP Offload | `#pragma omp target` | ICC `-qopenmp -qoffload=optional` | 中 | 低 |
| Intel LEO Offload | `#pragma offload target(mic)` | ICC | 中 | 中 |
| MIC Native OpenMP | `#pragma omp parallel` | ICC `-mmic -openmp` | 中-高 | 中 |
| MIC Native TBB | `tbb::parallel_for` | ICC `-mmic` + TBB 4.4 | 中-高 | 中 |
| MIC Native Cilk | `cilk_for` | ICC `-mmic -lcilkrts` | 中-高 | 中 |
| MIC Native MPI | `MPI_*` | ICC + Intel MPI 5.1 | 中-高 | 高 |
| MKL (Native) | `cblas_dgemm` | ICC `-mmic -lmkl` | 低 (46 GFLOPS) | 低 |
| **KNC Intrinsics** | `_mm512_fmadd_pd` | ICC `-mmic` | **最高** (575 GFLOPS) | 高 |
| GCC liboffloadmic | — | GCC 5.3.0 | — | ❌ 不可行 |

**推荐开发路径**: 
- 快速原型 → OpenMP offload
- 生产计算 → MIC Native OpenMP + KNC intrinsics
- 极致性能 → 手动 KNC AVX-512 intrinsics (16 accumulators 模式)

### 3.2 NEC VE 1.0 可用模型

| 模型 | 语法 | 编译 | 性能级别 | 复杂度 |
|------|------|------|---------|--------|
| VE Native (单核) | Standard C | `ncc -o prog prog.c` | 低 (~54 GFLOPS) | 低 |
| VE Native (多核) | `#pragma omp parallel` | `ncc -fopenmp` | 中 (~434 GFLOPS) | 中 |
| VE Native (Fortran) | nfort + opt(1800) | `nfort` | 中-高 (434 GFLOPS) | 中 |
| **NLC BLAS** | `cblas_dgemm` | 链接 `-lblas_openmp` | **最高** (1750 GFLOPS) | 低 |
| AVEO (Host侧) | `veo_proc_create` | `g++ -lveo` | 受 PCIe 限制 | 中 |
| NEC MPI (跨卡) | `mpicc` / `mpirun -ve 0-2` | NEC MPI 3.10.0 | 受 PCIe 限制 | 高 |
| OpenMP Offload (VE) | `#pragma omp target device(N)` | 需 LLVM/VE | 未验证 | 未知 |

**推荐开发路径**:
- 矩阵/向量运算 → NLC BLAS (cblas_dgemm)
- 自定义内核 → ncc/nfort + `#pragma omp parallel`
- 跨卡通信 → NEC MPI (仅 VE 间)
- Host 调度 → Python + AVEO

---

## 4. 异构协同模式设计

### 4.1 核心原则

**PCIe 最小化定律**: 数据一旦加载到加速卡，尽量在卡内完成所有计算。
Host↔Device 数据搬运次数必须最小化。

> 加速器总带宽 (4.4 TB/s) ÷ PCIe 上游带宽 (~40 GB/s, 估计) ≈ **110:1**
> 意味着每 1 次 PCIe 传输，可以在卡内完成 110 次同等数据量的运算。

### 4.2 三种协同模式

#### 模式 A: 数据中心型 (Data-Centric)

```
数据预处理 → PCIe 加载 → [核内计算 × N 轮] → PCIe 回传 → 结果汇总
```

- 适用: 训练、批处理、参数扫描
- PCIe 压力: 仅首尾各一次
- 每卡独立运行，互不通信
- 这是**效率最高的模式**

#### 模式 B: 流水线型 (Pipeline)

```
Phi (不规则预处理) → PCIe (中间数据) → VE×3 (稠密计算) → PCIe → 后处理
```

- Phi 做特征工程/图遍历 → VE 做矩阵运算
- 中间数据经 PCIe 传递 (受带宽限制)
- 需要中间数据量 << 最终计算量才有价值

#### 模式 C: MPI 跨卡 (仅VE间)

```
VE0 ←─MPI(1.52μs)─→ VE1 ←─MPI(1.52μs)─→ VE2
```

- 仅 VE 间可行 (NEC MPI)
- Phi 无法加入 MPI ring
- 适用: 梯度聚合 (AllReduce), 参数同步

### 4.3 任务-加速器匹配矩阵

| 工作负载类型 | 最佳加速器 | 原因 |
|-------------|-----------|------|
| 稠密矩阵乘法 (BLAS3) | **VE×3** | NLC 1750 GFLOPS/卡 vs Phi 63 GFLOPS |
| 稀疏矩阵/图遍历 | **Phi** | x86 缓存架构, 不规则访存友好 |
| 向量加法/点积 | **VE×3** | 编译器自动向量化 |
| K-means | **Phi** | B树状 clustering, 同步开销可控 |
| PageRank | **Phi** (遍历) + **VE×3** (matvec) | 混合流水线 |
| 特征值分解/SVD | **VE×3** (NLC LAPACK) | 库优化 |
| FFT/卷积 | **Phi** (MKL) | 已有优化库 |
| 概率统计模型 | **Phi** | 分支密集, 对向量不友好 |
| 排序 (Bitonic) | **Phi** | KNC intrinsics 充分优化 |

---

## 5. 功耗与散热

### 5.1 整机功耗预算

| 组件 | 数量 | 单卡 TDP | 小计 |
|------|------|---------|------|
| NEC VE 1.0 | 3 | ~300W | 900W |
| Xeon Phi 7120P | 1 | 300W | 300W |
| Xeon Gold 6252 | 2 | 150W | 300W |
| 风扇/主板/磁盘 | — | — | ~230W |
| **总计** | | | **~1730W** |

### 5.2 PSU 状态

| 场景 | DC 负载 | 单 PSU 负载 | 冗余状态 |
|------|---------|-----------|---------|
| 待机 | 450W | 28% | ✅ |
| 轻载 (1 VE + Phi) | 750W | 47% | ⚠️ 单 PSU 94% |
| 满载 (3 VE + Phi) | 1730W | 108% | ❌ 冗余失效 |

> **结论**: 1600W PSU 下，3VE+Phi 不能同时满载。必须通过软件功率封顶。

### 5.3 Phi 散热约束

- 被动散热: **必须放在 Slot 1** (最靠近进风口)
- Slot 2+: 被前面 VE 加热 → 降频至 30-50%
- 环境温度: ≤25°C

---

## 6. 参考文献

本分析基于以下已有文档：
- `intel_phi/README.md` — Phi 环境总览
- `intel_phi/docs/research/Xeon_Phi_7120P_Specific_Assessment.md` — Phi 专项评估
- `intel_phi/docs/research/ESC4000G4_7120P_Final_Assessment.md` — 整机评估
- `intel_phi/docs/impl/20260520_055400_peak_performance_verification.md` — Phi 峰值验证
- `intel_phi/docs/impl/20260520_231500_xeon_phi_offload_guide.md` — Phi Offload 指南
- `intel_phi/docs/research/20260521_075157_phibench_analysis.md` — PhiBench 分析
- `intel_phi/docs/research/20260522_031500_shamrock_knc_feasibility.md` — Shamrock 不可行分析
- `nec_ve/README.md` — VE 环境总览
- `nec_ve/NEC_VE_Installation_Report.md` — VE 安装报告
- `nec_ve/Three_Card_Performance_Plan.md` — 三卡方案
- `nec_ve/Three_Card_Test_Cases.md` — 测试案例
- `nec_ve/PSU_1600W_Assessment.md` — 电源评估
- `nec_ve/docs/research/20260520_034418_performance_gap_analysis.md` — VE 性能差距
- `nec_ve/docs/impl/20260520_053000_nlc_blas_validation.md` — NLC BLAS 验证
