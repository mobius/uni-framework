# Phi VirtIO 文件 I/O 调查报告

> 日期: 2026-06-04
> 来源: Parallel Programming And Optimization For Xeon Phi 第 1.2.6 节

---

## 1. 调查起因

书中提到 KNC 通过 VirtIO 访问 Host 文件，可用于消除 scp 步骤。

## 2. 现状

| 组件 | 状态 |
|------|------|
| `mic_virtblk` 模块 (Phi 侧) | ✅ 已加载 |
| VirtIO 地址 (内核参数) | ✅ `virtio_addr=0x8119ba0800` |
| Backing file | ✅ `/var/mpss/mic_virtblk.img` (2GB ext2) |
| `/dev/vda` 块设备 | ✅ 可用 |
| 自动挂载路径 | `/media/vda` |

## 3. 配置关键点

`ExtraCommandLine` 加 `virtblk_file=` **不够**——模块初始化时 Host 尚未注册文件。正确方式：

```bash
# 一次性: 创建镜像
sudo dd if=/dev/zero of=/var/mpss/mic_virtblk.img bs=1M count=2048
sudo mkfs.ext2 -F /var/mpss/mic_virtblk.img
sudo mount -o loop /var/mpss/mic_virtblk.img /mnt/temp
sudo chmod 777 /mnt/temp
sudo umount /mnt/temp

# 每次重启后:
sudo bash -c 'echo "/var/mpss/mic_virtblk.img" > /sys/class/mic/mic0/virtblk_file'
```

## 4. 性能对比 (SpMV 2MB CSR)

| 阶段 | /tmp (ramfs) | /media/vda (VirtIO) | Δ |
|------|-------------|---------------------|----|
| scp 上传 | 1.30s | 1.59s | +22% |
| Phi 分块 | 1.30s | 1.86s | +43% |
| scp 下载 | 1.60s | 1.61s | ~0 |
| VE SpMV | 0.10s | 0.10s | ~0 |
| **总计** | **4.31s** | **5.15s** | **+19%** |

## 5. 结论

- **小文件（<2MB）**: ramfs (/tmp) 更快——VirtIO 多一层块设备→ring→Host 开销
- **VirtIO 适用场景**: 数据 >16GB（超 Phi RAM）、持久化存储
- **当前项目**: scp + /tmp 方案已是最优
