# 示例 3: 串行流水线 (Pipeline)

严格串行依赖链: VE1 → VE2 → VE3 → Phi → Host。数据接力流经全部 5 个计算单元。

## 流水线

```
Host(gen) → VE1(dgemm) → VE2(scale) → VE3(transpose) → Phi(stats) → Host(report)
```

## 运行

```bash
cd uni
bash examples/pipeline/run.sh
```

## 与 Multi-Task 的对比

| | Multi-Task | Pipeline |
|---|-----------|---------|
| 拓扑 | 分叉-汇合 | 严格串行链 |
| 并行度 | 3 VE 并行 | 0 |
| 数据流 | 独立输入 | 上游→下游接力 |
