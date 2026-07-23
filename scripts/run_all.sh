#!/bin/bash
# run_all.sh — 一键验收脚本
# 运行全部基准测试和示例应用，输出统一定价报告
# Usage: bash scripts/run_all.sh 2>&1 | tee docs/impl/$(date +%Y%m%d_%H%M%S)_acceptance.log

set -e
set -o pipefail
cd "$(dirname "$0")/.."

PYTHON=./env/.venv/bin/python3
PASS=0; FAIL=0; TOTAL=0
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

check() {
    local name="$1"; shift
    TOTAL=$((TOTAL+1))
    echo; echo "==================== $TOTAL. $name ===================="
    if "$@" 2>&1 | tail -3; then
        PASS=$((PASS+1)); echo -e "${GREEN}✅ $name${NC}"
    else
        FAIL=$((FAIL+1)); echo -e "${RED}❌ $name${NC}"
    fi
}

echo "============================================================"
echo "  Uni 验收测试 — $(date '+%Y-%m-%d %H:%M')"
echo "============================================================"

# ── Phase 0: Hardware ──
check "硬件检查"  bash scripts/check_hw.sh

# ── Phase 3: Benchmarks ──
check "PCIe 带宽 (TC-001)" $PYTHON scripts/bench_pcie.py
check "数据中心吞吐 (TC-002)" $PYTHON scripts/bench_throughput.py
check "流水线延迟 (TC-003)" $PYTHON scripts/bench_pipeline_latency.py
check "MPI 扩展性 (TC-004)" $PYTHON scripts/bench_mpi.py

# ── Phase 4: Applications ──
check "异构 SpMV"         $PYTHON src/apps/hetero_spmv/spmv_app.py
check "数据预处理"         $PYTHON src/apps/hetero_dataprep/dataprep_app.py
check "Monte Carlo 定价"   $PYTHON src/apps/monte_carlo/mc_app.py

# ── Tests ──
check "单元测试 (13项)"   $PYTHON tests/test_scheduler.py

# ── Summary ──
echo
echo "============================================================"
echo "  验收结果: $PASS/$TOTAL 通过"
echo "============================================================"
[ $FAIL -eq 0 ] && echo -e "${GREEN}🎉 全部通过${NC}" || echo -e "${RED}⚠️  $FAIL 项失败${NC}"
