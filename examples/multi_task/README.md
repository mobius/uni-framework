# 示例 2: 多卡异构任务流 (Multi-Task)

演示有依赖关系的 DAG 任务在异构设备上的调度。

## 场景

```
Task0: Host 生成随机矩阵并分块
  │
  ├── Task1: VE1 矩阵乘法 (Block A)
  ├── Task2: VE2 矩阵乘法 (Block B)
  └── Task3: VE3 矩阵乘法 (Block C)
       │
       └── Task4: VE1 聚合三个块的结果
            │
            └── Task5: Phi 统计 (min/max/mean/stddev)
```

## 运行

```bash
cd uni
bash examples/multi_task/run.sh
```

## 预期输出

```
  ▶ gen [host]
  ✅ gen (0.1s)
  ▶ matmul_1 [ve1] (dep: ['gen'])
  ▶ matmul_2 [ve2] (dep: ['gen'])
  ▶ matmul_3 [ve3] (dep: ['gen'])
  ✅ matmul_1 (2.3s)
  ✅ matmul_2 (2.3s)
  ✅ matmul_3 (2.3s)
  ▶ aggregate [ve1] (dep: ['matmul_1', 'matmul_2', 'matmul_3'])
  ✅ aggregate (0.2s)
  ▶ stats [phi0]
  ✅ stats (0.5s)

  DAG 总耗时: ~3.1s
  ✅ 校验和匹配
  ✅ 全部任务通过
```

## 与 Basic 的对比

| 维度 | Basic | Multi-Task |
|------|-------|-----------|
| 任务数 | 4 独立 | 6 有依赖 |
| 调度 | asyncio.gather | DAG 拓扑排序 |
| 数据流 | 无 | 文件传递 |
| 负载类型 | 同构 FMA | 生成/DGEMM/聚合/统计 |
