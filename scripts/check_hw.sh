#!/bin/bash
# check_hw.sh — 异构计算硬件环境基线检查
# 用法: bash scripts/check_hw.sh 2>&1 | tee docs/research/$(date +%Y%m%d_%H%M%S)_hw_check.log
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass()  { echo -e "${GREEN}[PASS]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
info()  { echo -e "[INFO] $*"; }
header(){ echo; echo "=== $* ==="; }

# ─── 系统基础 ───
header "系统信息"
info "主机名: $(hostname)"
info "操作系统: $(cat /etc/rocky-release 2>/dev/null || cat /etc/os-release | head -1)"
info "内核: $(uname -r)"
echo

# ─── CPU & NUMA ───
header "CPU & NUMA 拓扑"
lscpu | grep -E "^Model name|^CPU\(s\)|^Thread|^Core|^Socket|^NUMA"
echo
if command -v numactl &>/dev/null; then
    numactl --hardware
else
    warn "numactl 未安装"
fi
echo

# ─── 内存 ───
header "内存"
free -h
echo
info "DIMM 信息:"
dmidecode -t memory 2>/dev/null | grep -E "^\s*(Size|Type|Speed|Locator):" | grep -v "No Module Installed" || warn "无法读取 DIMM 信息 (需 root)"
echo

# ─── PSU ───
header "电源"
if command -v ipmitool &>/dev/null; then
    ipmitool fru print 2>/dev/null | grep -A5 "Power Supply" || warn "ipmitool 无法读取 PSU 信息"
else
    warn "ipmitool 未安装, 跳过 PSU 检查"
fi
echo

# ─── NEC VE ───
header "NEC Vector Engine 1.0 状态"

# VE 设备检测
VE_COUNT=$(lspci 2>/dev/null | grep -c "NEC Corporation Vector Engine" || echo 0)
info "检测到 VE 卡数量: $VE_COUNT"

if [ "$VE_COUNT" -gt 0 ]; then
    lspci | grep "NEC Corporation Vector Engine"
    echo

    # PCIe 链路
    info "PCIe 链路状态:"
    for dev in $(lspci | grep "NEC" | awk '{print $1}'); do
        speed=$(lspci -vv -s "$dev" 2>/dev/null | grep "LnkSta:" | head -1 || echo "LnkSta: unknown (需 root)")
        numa=$(lspci -vv -s "$dev" 2>/dev/null | grep "NUMA" || echo "NUMA: unknown")
        info "  $dev: $speed | $numa"
    done
    echo

    # VEOS 服务 (AVEO 编号 1-3)
    info "VEOS 服务状态:"
    for i in 1 2 3; do
        if systemctl is-active ve-os-launcher@$i.service &>/dev/null; then
            pass "ve-os-launcher@$i.service: $(systemctl is-active ve-os-launcher@$i.service)"
        else
            fail "ve-os-launcher@$i.service: not active"
        fi
    done
    echo

    # 卡状态
    if command -v vecmd &>/dev/null; then
        info "VE 卡状态 (vecmd):"
        sudo /opt/nec/ve/bin/vecmd state get 2>/dev/null || warn "无法执行 vecmd (需 sudo)"
    fi
    echo

    # 固件版本 (sysfs 编号 0-2; numa_node 缺失时回退 lspci)
    info "固件版本:"
    for i in 0 1 2; do
        fw=$(cat /sys/class/ve/ve$i/fw_version 2>/dev/null || echo "N/A")
        node=$(cat /sys/class/ve/ve$i/numa_node 2>/dev/null || true)
        if [ -z "$node" ]; then
            pci_addr=$(readlink /sys/class/ve/ve$i/device 2>/dev/null | awk -F/ '{print $NF}')
            node=$(lspci -vv -s "$pci_addr" 2>/dev/null | grep "NUMA node" | awk '{print $NF}' || echo "N/A")
        fi
        info "  VE$i: fw=$fw, numa_node=$node"
    done
    echo

    # 编译器
    info "编译器版本:"
    ncc --version 2>/dev/null | head -1 || warn "ncc 未安装"
    nfort --version 2>/dev/null | head -1 || warn "nfort 未安装"
else
    warn "未检测到 NEC VE 卡"
fi
echo

# ─── Intel Xeon Phi ───
header "Intel Xeon Phi 7120P 状态"

PHI_COUNT=$(lspci 2>/dev/null | grep -c "Intel.*Xeon Phi\|Intel.*Co-processor" || echo 0)
info "检测到 Phi 卡数量: $PHI_COUNT"

if [ "$PHI_COUNT" -gt 0 ]; then
    lspci | grep -E "Intel.*Xeon Phi|Intel.*Co-processor"

    # MPSS 状态
    info "MPSS 服务状态:"
    if systemctl is-active mpss &>/dev/null; then
        pass "mpss: $(systemctl is-active mpss)"
    else
        fail "mpss: not active"
    fi
    echo

    # micctrl
    if command -v micctrl &>/dev/null; then
        micctrl --status 2>/dev/null || warn "micctrl 无法获取状态"
    fi
    echo

    # micinfo
    if command -v micinfo &>/dev/null; then
        info "micinfo 摘要:"
        micinfo 2>/dev/null | grep -E "Device No|Cores|Threads|Memory|Flash" || warn "micinfo 不可用"
    fi
    echo

    # Phi 温度 (micinfo 在 Host 侧上报, 卡内无 /sys/class/thermal)
    info "Phi 温度:"
    if command -v micinfo &>/dev/null; then
        micinfo 2>/dev/null | grep "Die Temp" | awk -F: '{printf "  Die Temp:%s\n", $2}' \
            || warn "micinfo 无法读取温度"
    else
        warn "micinfo 未安装"
    fi
fi
echo

# ─── PCIe 拓扑 ───
header "PCIe 拓扑"
lspci | grep -E "NEC|Intel.*Phi|Non-Volatile|VGA|Ethernet" || true
echo

# ─── 汇总 ───
header "检查汇总"
echo "  VE 卡: $VE_COUNT 张"
echo "  Phi 卡: $PHI_COUNT 张"
echo
echo "检查完成。如有 FAIL/WARN，请对照 docs/plan/20260601_090918_development_roadmap.md 中的通过标准。"
