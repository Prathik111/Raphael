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