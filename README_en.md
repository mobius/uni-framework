# Uni — Intel Phi 7120P + NEC VE 1.0×3 Heterogeneous Computing Project

> Server: ASUS ESC4000 G4, 2× Xeon Gold 6252, Rocky Linux 8.10
> Accelerators: 1× Intel Xeon Phi 7120P (KNC) + 3× NEC Vector Engine 1.0

## Objective

Heterogeneous compute co-scheduling on a single ESC4000 G4 server, maximizing the complementary compute characteristics of Phi and VE.

## Compute Capacity

| Metric | Phi 7120P | VE 1.0×3 | **Total** |
|------|----------|---------|---------|
| FP64 Theoretical | 1.21 TFLOPS | 6.48 TFLOPS | **7.69 TFLOPS** |
| FP64 Achievable | 0.58 TFLOPS | 5.25 TFLOPS | **5.83 TFLOPS** |
| Memory | 16 GB GDDR5 | 144 GB HBM2 | **160 GB** |
| Memory BW | 157 GB/s | 3,186 GB/s | **3,343 GB/s** |

## Progress

| Phase | Content | Status |
|-------|------|------|
| 0 | Hardware verification & baseline | ✅ |
| 1 | Software stack (uv/ncc/ICC) | ✅ |
| 2 | Core scheduler (7 modules) | ✅ |
| 3 | Benchmarks (TC-001~006) | ✅ 4/6 passed, 5/6 noted |
| 4 | Applications (SpMV + Prep + MC) | ✅ |

## Quick Start

```bash
# 1. Hardware check
bash scripts/check_hw.sh

# 2. Python environment (uv)
cd env && uv venv && source .venv/bin/activate && uv pip install numpy rich
cd ..

# 3. Basic verification
bash examples/basic/run.sh          # 4-card parallel baseline (3,277 GFLOPS)

# 4. Benchmarks
./env/.venv/bin/python3 scripts/bench_throughput.py # 5.68 TFLOPS
./env/.venv/bin/python3 scripts/bench_pcie.py       # PCIe bandwidth
./env/.venv/bin/python3 scripts/bench_mpi.py        # MPI scalability

# 5. Applications
./env/.venv/bin/python3 src/apps/hetero_spmv/spmv_app.py
./env/.venv/bin/python3 src/apps/hetero_dataprep/dataprep_app.py
./env/.venv/bin/python3 src/apps/monte_carlo/mc_app.py

# 6. Full acceptance
bash scripts/run_all.sh 2>&1 | tee acceptance.log
```

## Benchmark Results

| Test | Metric | Result | Verdict |
|------|------|------|------|
| TC-001 PCIe BW | 3VE concurrent H2D | 13.7 GB/s (86% eff) | ⚠️ |
| TC-002 Throughput | 4-card parallel | **5.68 TFLOPS** | ✅ |
| TC-003 Pipeline Latency | Phi transit overhead | 569% (startup bottleneck) | ⚠️ |
| TC-004 MPI Scalability | 3-card ring efficiency | **97.8%** (VE2-adjusted) | ✅ |

## Applications

| App | Path | Flow | Result |
|------|------|------|------|
| Hetero SpMV | `src/apps/hetero_spmv/` | Host→Phi partition→3VE parallel | 0.107s, max_diff 1.07e-14 |
| Data Prep | `src/apps/hetero_dataprep/` | Phi clean→VE1 std→VE2 PCA | corr 0.997, std diff 3.55e-15 |
| Monte Carlo | `src/apps/monte_carlo/` | Phi paths→3VE payoff discount | diff 0.15% vs numpy |

## Scheduler Architecture

```
TaskGraph (DAG + power capping)
  ├── DeviceMgr → auto-detect Phi + 3×VE
  ├── NUMABinder → numactl affinity binding
  ├── PowerCap → 1440W effective budget
  ├── PhiRunner → micnativeloadex + scp I/O
  ├── VERunner → ve_exec file passthrough
  └── Profiler → estimate vs. actual comparison
```

## Key Constraints

- **PCIe Gen3 ×16**: Only 15.75 GB/s vs. 4.4 TB/s internal BW (~280:1 ratio)
- **PSU 1600W**: Full load 1730W exceeds rating (PowerCap: 1440W budget)
- **Phi passive cooling**: Must be in Slot 1 (nearest intake)
- **Incompatible programming models**: ICC 16.0 vs. ncc 5.4.1, no unified framework
- **Phi file I/O**: Requires scp bidirectional transfer; no filesystem passthrough (unlike VE)

## Core Strategy

1. Minimize PCIe traffic — compute locally on-card after data load
2. Task-characteristic matching — dense compute on VE, irregular access on Phi
3. Python scheduling layer — asyncio DAG task graph + NUMA affinity + power capping
4. uv-first — never pollute the global Python environment
5. Phi I/O via scp — micnativeloadex has no shared filesystem (VirtIO investigated, /tmp faster)
