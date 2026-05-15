from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)

MAX_PARALLEL_STEPS = 4
FALLBACK_RETRY_LIMIT = 2
RESOURCE_GPU_MEMORY_MB = 4096
RESOURCE_CPU_CORES = 4


class NodeState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepResourceType(StrEnum):
    CPU = "cpu"
    GPU = "gpu"
    NETWORK = "network"
    PLATFORM = "platform"


@dataclass
class StepResourceProfile:
    step_id: str
    resource_type: StepResourceType = StepResourceType.CPU
    gpu_memory_mb: int = 0
    cpu_cores: float = 0.5
    network_concurrency: int = 1
    platform_constraint: str | None = None


@dataclass
class DAGNode:
    step_id: str
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    state: NodeState = NodeState.PENDING
    resource_profile: StepResourceProfile | None = None
    fallback_step_id: str | None = None
    retry_count: int = 0
    max_retries: int = 2
    metadata: dict = field(default_factory=dict)


@dataclass
class ScheduledBatch:
    layer_index: int
    step_ids: list[str] = field(default_factory=list)
    gpu_steps: list[list[str]] = field(default_factory=list)
    cpu_steps: list[str] = field(default_factory=list)
    estimated_duration_ms: float = 0.0


@dataclass
class DAGExecutionResult:
    total_steps: int
    completed_steps: int
    failed_steps: int
    skipped_steps: int
    fallback_triggered: int
    parallelism_ratio: float
    execution_layers: int
    total_duration_ms: float


class WorkflowDAG:
    def __init__(self):
        self._nodes: dict[str, DAGNode] = {}
        self._edges: dict[str, list[str]] = defaultdict(list)
        self._reverse_edges: dict[str, list[str]] = defaultdict(list)

    def add_node(self, step_id: str, dependencies: list[str] | None = None, **kwargs) -> DAGNode:
        node = DAGNode(step_id=step_id, dependencies=list(dependencies or []), **kwargs)
        self._nodes[step_id] = node

        for dep in node.dependencies:
            self._edges[dep].append(step_id)
            self._reverse_edges[step_id].append(dep)
            if dep in self._nodes:
                self._nodes[dep].dependents.append(step_id)

        for dep in node.dependencies:
            if dep in self._nodes and step_id not in self._nodes[dep].dependents:
                    self._nodes[dep].dependents.append(step_id)

        return node

    def remove_node(self, step_id: str) -> None:
        if step_id not in self._nodes:
            return

        node = self._nodes.pop(step_id)

        for dep in node.dependencies:
            if dep in self._edges:
                self._edges[dep] = [s for s in self._edges[dep] if s != step_id]
            if dep in self._nodes:
                self._nodes[dep].dependents = [s for s in self._nodes[dep].dependents if s != step_id]

        for dependent in node.dependents:
            if dependent in self._reverse_edges:
                self._reverse_edges[dependent] = [s for s in self._reverse_edges[dependent] if s != step_id]
            if dependent in self._nodes:
                self._nodes[dependent].dependencies = [s for s in self._nodes[dependent].dependencies if s != step_id]

    def add_edge(self, from_step: str, to_step: str) -> None:
        if from_step not in self._nodes or to_step not in self._nodes:
            return

        if to_step not in self._edges[from_step]:
            self._edges[from_step].append(to_step)
        if from_step not in self._reverse_edges[to_step]:
            self._reverse_edges[to_step].append(from_step)

        if from_step not in self._nodes[to_step].dependencies:
            self._nodes[to_step].dependencies.append(from_step)
        if to_step not in self._nodes[from_step].dependents:
            self._nodes[from_step].dependents.append(to_step)

    def remove_edge(self, from_step: str, to_step: str) -> None:
        self._edges[from_step] = [s for s in self._edges.get(from_step, []) if s != to_step]
        self._reverse_edges[to_step] = [s for s in self._reverse_edges.get(to_step, []) if s != from_step]

        if from_step in self._nodes:
            self._nodes[from_step].dependents = [s for s in self._nodes[from_step].dependents if s != to_step]
        if to_step in self._nodes:
            self._nodes[to_step].dependencies = [s for s in self._nodes[to_step].dependencies if s != from_step]

    def get_node(self, step_id: str) -> DAGNode | None:
        return self._nodes.get(step_id)

    @property
    def nodes(self) -> dict[str, DAGNode]:
        return self._nodes

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    def has_cycle(self) -> bool:
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            for neighbor in self._edges.get(node_id, []):
                if neighbor not in self._nodes:
                    continue
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True

            rec_stack.discard(node_id)
            return False

        return any(node_id not in visited and dfs(node_id) for node_id in self._nodes)


class DynamicTopologicalSorter:
    def __init__(self, dag: WorkflowDAG):
        self._dag = dag

    def compute_layers(self) -> list[list[str]]:
        in_degree: dict[str, int] = {}
        for step_id, node in self._dag.nodes.items():
            in_degree[step_id] = len(node.dependencies)

        layers = []
        remaining = set(self._dag.nodes.keys())

        while remaining:
            ready = {n for n in remaining if in_degree.get(n, 0) == 0}
            if not ready:
                logger.warning("Cycle detected in DAG, breaking remaining nodes")
                ready = {min(remaining, key=lambda n: in_degree.get(n, 0))}

            layers.append(sorted(ready))

            for node_id in ready:
                remaining.discard(node_id)
                node = self._dag.get_node(node_id)
                if node:
                    for dependent in node.dependents:
                        in_degree[dependent] = max(0, in_degree.get(dependent, 0) - 1)

        return layers

    def get_ready_nodes(self, completed: set[str], running: set[str]) -> list[str]:
        ready = []
        for step_id, node in self._dag.nodes.items():
            if node.state != NodeState.PENDING:
                continue
            if step_id in running:
                continue
            if all(dep in completed for dep in node.dependencies):
                ready.append(step_id)
        return ready


class ResourceAwareScheduler:
    def __init__(
        self,
        max_parallel: int = MAX_PARALLEL_STEPS,
        gpu_memory_mb: int = RESOURCE_GPU_MEMORY_MB,
        cpu_cores: int = RESOURCE_CPU_CORES,
    ):
        self._max_parallel = max_parallel
        self._gpu_memory_mb = gpu_memory_mb
        self._cpu_cores = cpu_cores

    def schedule(self, layers: list[list[str]], profiles: dict[str, StepResourceProfile]) -> list[ScheduledBatch]:
        batches = []
        for layer_idx, layer in enumerate(layers):
            gpu_steps = [
                s for s in layer
                if profiles.get(s, StepResourceProfile(s)).resource_type == StepResourceType.GPU
            ]
            cpu_steps = [
                s for s in layer
                if profiles.get(s, StepResourceProfile(s)).resource_type != StepResourceType.GPU
            ]

            gpu_batches = self._bin_pack_gpu(gpu_steps, profiles)

            batches.append(ScheduledBatch(
                layer_index=layer_idx,
                step_ids=layer,
                gpu_steps=gpu_batches,
                cpu_steps=cpu_steps,
                estimated_duration_ms=self._estimate_duration(layer, profiles),
            ))

        return batches

    def _bin_pack_gpu(self, steps: list[str], profiles: dict[str, StepResourceProfile]) -> list[list[str]]:
        if not steps:
            return []

        bins: list[list[str]] = []
        current_bin: list[str] = []
        current_usage = 0

        sorted_steps = sorted(
            steps,
            key=lambda s: profiles.get(s, StepResourceProfile(s)).gpu_memory_mb,
            reverse=True,
        )

        for step_id in sorted_steps:
            profile = profiles.get(step_id, StepResourceProfile(step_id))
            needed = profile.gpu_memory_mb

            if needed > self._gpu_memory_mb:
                bins.append([step_id])
                continue

            if current_usage + needed <= self._gpu_memory_mb:
                current_bin.append(step_id)
                current_usage += needed
            else:
                if current_bin:
                    bins.append(current_bin)
                current_bin = [step_id]
                current_usage = needed

        if current_bin:
            bins.append(current_bin)

        return bins

    def _estimate_duration(self, step_ids: list[str], profiles: dict[str, StepResourceProfile]) -> float:
        base_ms = 1000.0
        gpu_count = sum(
            1 for s in step_ids
            if profiles.get(s, StepResourceProfile(s)).resource_type == StepResourceType.GPU
        )
        if gpu_count > 1:
            gpu_batches = self._bin_pack_gpu(
                [s for s in step_ids if profiles.get(s, StepResourceProfile(s)).resource_type == StepResourceType.GPU],
                profiles,
            )
            return base_ms * len(gpu_batches)
        return base_ms


class DAGParallelExecutor:
    def __init__(
        self,
        max_parallel: int = MAX_PARALLEL_STEPS,
        fallback_enabled: bool = True,
    ):
        self._max_parallel = max_parallel
        self._fallback_enabled = fallback_enabled
        self._dag = WorkflowDAG()
        self._sorter: DynamicTopologicalSorter | None = None
        self._scheduler = ResourceAwareScheduler(max_parallel=max_parallel)
        self._step_profiles: dict[str, StepResourceProfile] = {}
        self._completed: set[str] = set()
        self._failed: set[str] = set()
        self._skipped: set[str] = set()
        self._running: set[str] = set()
        self._fallback_count = 0
        self._step_results: dict[str, dict] = {}
        self._start_time: float = 0.0

    def build_from_flow(self, steps: list) -> None:
        self._dag = WorkflowDAG()

        for step in steps:
            step_id = step.id
            dependencies = list(step.depends_on) if hasattr(step, "depends_on") else []
            resource_type = StepResourceType.CPU
            gpu_memory = 0

            if hasattr(step, "target") and step.target:
                strategy = getattr(step.target, "strategy", None)
                if strategy and hasattr(strategy, "value") and "image" in strategy.value:
                    resource_type = StepResourceType.GPU
                    gpu_memory = 512

            profile = StepResourceProfile(
                step_id=step_id,
                resource_type=resource_type,
                gpu_memory_mb=gpu_memory,
            )
            self._step_profiles[step_id] = profile

            self._dag.add_node(step_id, dependencies=dependencies, resource_profile=profile)

        self._sorter = DynamicTopologicalSorter(self._dag)

    def inject_fallback(self, failed_step_id: str, fallback_step) -> str:
        if not self._fallback_enabled:
            return ""

        fallback_id = f"fallback_{failed_step_id}_{uuid.uuid4().hex[:6]}"
        failed_node = self._dag.get_node(failed_step_id)

        dependencies = list(failed_node.dependencies) if failed_node else []

        profile = StepResourceProfile(step_id=fallback_id, resource_type=StepResourceType.CPU)
        self._step_profiles[fallback_id] = profile

        self._dag.add_node(fallback_id, dependencies=dependencies, resource_profile=profile)

        if failed_node:
            for dependent_id in failed_node.dependents:
                self._dag.add_edge(fallback_id, dependent_id)

        self._fallback_count += 1
        logger.info(f"Injected fallback step {fallback_id} for failed step {failed_step_id}")

        return fallback_id

    def rewire_dependency(self, from_step: str, old_to: str, new_to: str) -> None:
        self._dag.remove_edge(from_step, old_to)
        self._dag.add_edge(from_step, new_to)
        logger.info(f"Rewired dependency: {from_step} -> {new_to} (was {old_to})")

    async def execute_parallel(
        self,
        step_executor_fn,
        on_step_complete=None,
        on_step_failed=None,
    ) -> DAGExecutionResult:
        self._completed = set()
        self._failed = set()
        self._skipped = set()
        self._running = set()
        self._step_results = {}
        self._start_time = asyncio.get_event_loop().time()

        if not self._sorter:
            self._sorter = DynamicTopologicalSorter(self._dag)

        total_steps = self._dag.node_count
        layers = self._sorter.compute_layers()
        scheduled = self._scheduler.schedule(layers, self._step_profiles)

        for batch in scheduled:
            ready_steps = self._get_executable_steps(batch)

            while ready_steps:
                batch_to_run = ready_steps[:self._max_parallel]
                self._running.update(batch_to_run)

                for node_id in batch_to_run:
                    node = self._dag.get_node(node_id)
                    if node:
                        node.state = NodeState.RUNNING

                tasks = []
                for step_id in batch_to_run:
                    task = asyncio.create_task(
                        self._execute_single_step(step_id, step_executor_fn)
                    )
                    tasks.append(task)

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for step_id, result in zip(batch_to_run, results, strict=False):
                    self._running.discard(step_id)

                    if isinstance(result, Exception):
                        self._handle_step_failure(step_id, result, on_step_failed)
                    elif result.get("success"):
                        self._handle_step_success(step_id, result, on_step_complete)
                    else:
                        self._handle_step_failure(step_id, RuntimeError(result.get("error", "Unknown")), on_step_failed)

                ready_steps = self._sorter.get_ready_nodes(self._completed, self._running)
                ready_steps = [s for s in ready_steps if s not in self._failed and s not in self._skipped]

        elapsed = (asyncio.get_event_loop().time() - self._start_time) * 1000

        return DAGExecutionResult(
            total_steps=total_steps,
            completed_steps=len(self._completed),
            failed_steps=len(self._failed),
            skipped_steps=len(self._skipped),
            fallback_triggered=self._fallback_count,
            parallelism_ratio=len(self._completed) / max(len(layers), 1),
            execution_layers=len(layers),
            total_duration_ms=elapsed,
        )

    async def _execute_single_step(self, step_id: str, executor_fn) -> dict:
        try:
            result = await executor_fn(step_id)
            return result if isinstance(result, dict) else {"success": bool(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_step_success(self, step_id: str, result: dict, callback=None) -> None:
        self._completed.add(step_id)
        self._step_results[step_id] = result

        node = self._dag.get_node(step_id)
        if node:
            node.state = NodeState.COMPLETED

        if callback:
            try:
                callback(step_id, result)
            except Exception as e:
                logger.debug(f"Step complete callback error: {e}")

    def _handle_step_failure(self, step_id: str, error: Exception, callback=None) -> None:
        node = self._dag.get_node(step_id)
        if node:
            node.retry_count += 1

            if node.retry_count < node.max_retries:
                node.state = NodeState.PENDING
                logger.info(f"Retrying step {step_id} (attempt {node.retry_count + 1})")
                return

        self._failed.add(step_id)
        if node:
            node.state = NodeState.FAILED

        for dependent_id in list(node.dependents) if node else []:
            dep_node = self._dag.get_node(dependent_id)
            if dep_node:
                dep_node.state = NodeState.SKIPPED
                self._skipped.add(dependent_id)

        if callback:
            try:
                callback(step_id, {"error": str(error)})
            except Exception as e:
                logger.debug(f"Step failed callback error: {e}")

    def _get_executable_steps(self, batch: ScheduledBatch) -> list[str]:
        executable = []
        for step_id in batch.step_ids:
            if step_id in self._completed or step_id in self._failed or step_id in self._skipped:
                continue
            node = self._dag.get_node(step_id)
            if node and node.state == NodeState.PENDING and all(dep in self._completed for dep in node.dependencies):
                    executable.append(step_id)
        return executable

    @property
    def dag(self) -> WorkflowDAG:
        return self._dag

    @property
    def completed_steps(self) -> set[str]:
        return self._completed.copy()

    @property
    def failed_steps(self) -> set[str]:
        return self._failed.copy()
