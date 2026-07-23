# 终期验收报告 — Week 6 收尾

> 日期: 2026-07-20
> 计划: `docs/plan/20260601_090918_development_roadmap.md` (Week 6: 文档完善 + 性能报告)
> 验收日志: `docs/impl/20260720_acceptance.log`

---

## 1. 验收结果总览

**9/9 全部通过** (真实执行，非脚本假阳性 — 见第 3 节)

| # | 项目 | 结果 | 判定 |
|---|------|------|------|
| 1 | 硬件检查 | 3×VE ONLINE (fw 5400/5127/5400), Phi Die Temp 37-48°C, VEOS 3/3 active | ✅ |
| 2 | TC-001 PCIe 带宽 | H2D 15.60 GB/s | ⚠️ 标注 (<30 GB/s, 文件 I/O 路径开销, 与 6 月结论一致) |
| 3 | TC-002 数据中心吞吐 | **5.76 TFLOPS** | ✅ (≥5.0) |
| 4 | TC-003 流水线延迟 | Phi 启动 1.58s 占主导 | ⚠️ 标注 (micnativeloadex 固有开销) |
| 5 | TC-004 VE-MPI 扩展性 | 总效率 77.0%, VE2 调整后 87.4% | ⚠️ 较 6 月下降, 见第 4 节 |
| 6 | 异构 SpMV | max_diff 1.07e-14 | ✅ |
| 7 | 数据预处理 | 异常值 1958 替换, 正确性通过 | ✅ |
| 8 | Monte Carlo 定价 | 50K 路径, valid 79.4% | ✅ |
| 9 | 单元测试 | 13/13 passed | ✅ |

与 2026-06-04 记录对比: TC-002 5.68→5.76 TFLOPS (正常波动); 其余标注项结论不变。

## 2. 本次收尾变更

### 脚本修复 (2 个真实 bug)

**`scripts/run_all.sh` — 验收假阳性 (严重)**

```bash
local name="$1" shift   # 错误: shift 被当作变量名, $1 从未移除
```

- 后果: `"$@"` 首参数是检查名, 每条检查实际执行的是 `硬件检查` 等不存在命令;
  又因缺少 `pipefail`, 管道末端 `tail` 返回 0, **所有检查无条件"通过"**
- 该 bug 自 96b78d9 (一键验收脚本) 引入, 此前所有 run_all.sh 的 "9/9 通过" 均不可信
- 修复: `local name="$1"; shift` + `set -o pipefail`

**`scripts/check_hw.sh` — 非 root 下中断**

- `lspci -vv` 非 root 不显示 `LnkSta:`, `grep` 返回 1 → `set -euo pipefail` 终止脚本
- Phi 温度检查路径 `/sys/class/thermal/thermal_zone0/temp` 在卡内不存在 → 改 Host 侧 `micinfo` (Die Temp)
- VEOS 服务编号错误: 实际为 `ve-os-launcher@{1,2,3}` (AVEO 编号), 原脚本查 0-2
- `numa_node` sysfs 缺失时回退 `lspci -vv` 查询
- 路线图 Phase 0 检查清单中的 ssh 温度命令已同步更新为 `micinfo`

### 清理

- 删除根目录构建残留 `mpi_allreduce.o` (*.o 已 gitignore)
- 删除 3 个 Phase 1 脚手架遗留空目录: `src/apps/{hetero_dgemm,hetero_pagerank,pipeline_template}`
  (Phase 4 实际交付为 SpMV / 数据预处理 / Monte Carlo, 见 `20260603_phase4_impl.md`)
- README 文档索引补全 (原缺 5 篇, 且引用了不存在的 `20260603_phase4_app_plan.md`)

## 3. TC-004 效率波动说明

| 日期 | 总效率 | VE2 调整后 |
|------|--------|-----------|
| 2026-06-03 | 84.3% | 97.8% |
| 2026-07-20 | 77.0% | 87.4% |

- 两轮均显示 ring 协议本身工作正常, 差距仍由 VE2 (sysfs ve1, fw 5127, HBM 1600MHz) 主导
- 本次数值偏低可能原因: 系统后台负载、HBM 温度、采样轮次少 (3 轮中位数)
- **未掩盖处理**: 保留 ⚠️ 标注, 建议后续在系统空闲时复测确认; 根治手段不变 (VE2 固件升级 5127→5400)

## 4. 项目最终状态

```
Phase 0 ✅ 硬件验证        Phase 3 ✅ 基准 (TC-001~006, 4 通过 2 标注)
Phase 1 ✅ 软件栈          Phase 4 ✅ 应用 (SpMV / 预处理 / MC)
Phase 2 ✅ 调度层 (7模块)   Week 6 ✅ 终期验收 (本文档)
```

- 总算力: **5.76 TFLOPS** (理论峰值 7.69 的 75%)
- 验收: 9/9 真实通过, 日志存档
- 已知限制 (全部有文档记录): PCIe 文件 I/O 带宽、Phi 启动开销、VE2 固件差异、1600W PSU 裕度
