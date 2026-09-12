"""Small deterministic benchmark harness for the local AI ecosystem."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class BenchmarkResult:
    name: str
    category: str
    iterations: int
    elapsed_s: float
    ops_per_s: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkReport:
    generated_at: float
    results: list[BenchmarkResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "results": [r.__dict__ for r in self.results],
        }

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def measure(
    name: str,
    category: str,
    iterations: int,
    fn: Callable[[int], Any],
) -> BenchmarkResult:
    start = time.perf_counter()
    for i in range(iterations):
        fn(i)
    elapsed = max(time.perf_counter() - start, 1e-9)
    return BenchmarkResult(
        name=name,
        category=category,
        iterations=iterations,
        elapsed_s=elapsed,
        ops_per_s=iterations / elapsed,
    )


class BenchmarkRunner:
    def __init__(self) -> None:
        self._cases: list[tuple[str, str, int, Callable]] = []
        self.context: dict[str, Any] = {}

    def add(
        self, name: str, category: str, iterations: int, fn: Callable[[dict, int], Any]
    ) -> None:
        """Register a case (fn receives (context, iteration))."""
        self._cases.append((name, category, iterations, fn))

    def run(self) -> BenchmarkReport:
        """Execute every case in registration order."""
        report = BenchmarkReport(generated_at=time.time())
        for name, category, iterations, fn in self._cases:
            report.results.append(
                measure(name, category, iterations, lambda i, fn=fn: fn(self.context, i))
            )
        return report


def default_suite() -> BenchmarkRunner:
    """The standard suite wiring real subsystems (in-memory where noted)."""
    from ai_ecosystem.agent.executor import ParallelExecutor
    from ai_ecosystem.core.models import Task
    from ai_ecosystem.core.persistence import Database, SqliteTaskRepository

    runner = BenchmarkRunner()
    db = Database(":memory:")
    repo = SqliteTaskRepository(db)

    def create_task(ctx: dict, i: int) -> None:
        repo.create(Task(title=f"bench-{i}"))

    runner.add("sqlite_create_task", "persistence", 1000, create_task)

    def noop(ctx: dict, i: int) -> None:
        return None

    runner.add("python_noop", "runtime", 10000, noop)

    executor = ParallelExecutor(max_workers=4)

    def executor_submit(ctx: dict, i: int) -> None:
        executor.submit(lambda: i)

    runner.add("parallel_submit", "execution", 1000, executor_submit)
    return runner
