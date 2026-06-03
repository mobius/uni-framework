"""
通用 DAG 任务图调度器
task_graph.py — Phase 2 核心: 拓扑排序 + 并行执行 + 依赖管理
"""

import asyncio
import time
import subprocess
import os
from dataclasses import dataclass, field
from typing import Optional, Callable


@dataclass
class TaskNode:
    name: str
    device: str          # "host" | "phi0" | "ve1" | "ve2" | "ve3"
    run_fn: Callable     # async callable that returns dict
    depends_on: list[str] = field(default_factory=list)
    
    # Runtime state
    status: str = "pending"   # pending | ready | running | done | failed
    result: Optional[dict] = None
    start_time: float = 0.0
    end_time: float = 0.0


class TaskGraph:
    """有向无环图任务调度器
    
    用法:
        graph = TaskGraph()
        graph.add(TaskNode("gen", "host", gen_fn))
        graph.add(TaskNode("matmul", "ve1", matmul_fn, depends_on=["gen"]))
        results = await graph.execute()
    """
    
    def __init__(self):
        self.nodes: dict[str, TaskNode] = {}
    
    def add(self, node: TaskNode):
        self.nodes[node.name] = node
    
    async def execute(self, verbose: bool = True) -> dict:
        """执行 DAG，返回 {task_name: result_dict}"""
        pending = set(self.nodes.keys())
        running: dict[str, asyncio.Task] = {}
        results: dict[str, dict] = {}
        
        if verbose:
            print(f"\n{'='*60}")
            print(f"  TaskGraph: {len(self.nodes)} 个任务")
            self._print_dag()
            print(f"{'='*60}\n")
        
        start_all = time.time()
        
        while pending or running:
            # 找出所有就绪任务 (依赖已满足)
            ready = []
            for name in list(pending):
                node = self.nodes[name]
                if all(dep in results for dep in node.depends_on):
                    ready.append(name)
            
            # 启动就绪任务
            for name in ready:
                pending.remove(name)
                node = self.nodes[name]
                node.status = "running"
                node.start_time = time.time()
                
                if verbose:
                    deps_str = f" (dep: {node.depends_on})" if node.depends_on else ""
                    print(f"  ▶ {name} [{node.device}]{deps_str}")
                
                running[name] = asyncio.create_task(
                    self._run_node(name, node)
                )
            
            # 如果没有 ready 也没有 running，死锁
            if not ready and not running:
                stuck = pending
                print(f"  ❌ DAG 死锁! 阻塞任务: {stuck}")
                break
            
            # 等待至少一个任务完成
            if running:
                done, _ = await asyncio.wait(
                    running.values(),
                    return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    # 找到对应的 task name
                    for name, t in list(running.items()):
                        if t is task:
                            node = self.nodes[name]
                            node.end_time = time.time()
                            elapsed = node.end_time - node.start_time
                            
                            try:
                                node.result = task.result()
                                node.status = "done"
                                results[name] = node.result
                                
                                status_icon = "✅" if node.result.get("status") == "pass" else "⚠️"
                                if verbose:
                                    print(f"  {status_icon} {name} ({elapsed:.1f}s)")
                            except Exception as e:
                                node.status = "failed"
                                node.result = {"status": "fail", "error": str(e)}
                                results[name] = node.result
                                if verbose:
                                    print(f"  ❌ {name} FAILED: {e}")
                            
                            del running[name]
                            break
        
        total_time = time.time() - start_all
        
        if verbose:
            print(f"\n  DAG 总耗时: {total_time:.1f}s")
            self._print_summary(results)
        
        return results
    
    async def _run_node(self, name: str, node: TaskNode):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, node.run_fn)
    
    def _print_dag(self):
        for name, node in self.nodes.items():
            deps = " → ".join(node.depends_on) if node.depends_on else "(无)"
            print(f"    {name} [{node.device}] ← {deps}")
    
    def _print_summary(self, results: dict):
        print(f"\n  {'任务':<12} {'设备':<8} {'状态':<8} {'耗时'}")
        print(f"  {'-'*40}")
        for name, node in self.nodes.items():
            elapsed = node.end_time - node.start_time if node.end_time else 0
            status = node.status
            icon = {"done":"✅","failed":"❌","running":"⏳"}.get(status, "?")
            print(f"  {name:<12} {node.device:<8} {icon} {status:<5} {elapsed:.1f}s")
        
        total_pass = sum(1 for n in self.nodes.values() if n.status == "done")
        total_tasks = len(self.nodes)
        print(f"\n  完成: {total_pass}/{total_tasks}")
