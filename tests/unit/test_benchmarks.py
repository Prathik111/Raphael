"""Gate 41: benchmarks measure reality and persist machine-readable JSON."""

import json

from ai_ecosystem.bench import BenchmarkRunner, default_suite, measure


def test_benchmark_runner_unit():
    runner = BenchmarkRunner()
    runner.add("noop", "test", 10, lambda ctx, i: None)
    report = runner.run()
    assert len(report.results) == 1
    result = report.results[0]
    assert result.iterations == 10
    assert result.average_ms >= 0
    assert result.min_ms <= result.average_ms <= result.max_ms


def test_full_suite_produces_machine_readable_json(tmp_path):
    out = str(tmp_path / "bench.json")
    report = default_suite().run()
    report.write_json(out)
    loaded = json.loads(open(out).read())
    assert loaded["generated_at"] > 0
    names = {entry["name"] for entry in loaded["results"]}
    for expected in (
        "task.create",
        "persistence.roundtrip",
        "event.publish",
        "plan.validate",
        "tool.execute",
        "dag.execute",
        "verify.command",
        "memory.insert",
        "memory.retrieve",
        "skill.lookup",
        "skill.discover",
        "agent.delegate",
        "message.delivery",
        "provider.select",
        "sync.manifest",
        "workspace.update",
        "viz.ingest",
        "scheduler.tick",
    ):
        assert expected in names, expected
    for entry in loaded["results"]:
        assert entry["iterations"] > 0
        assert entry["average_ms"] >= 0
        # Smoke bound: trivial ops must not take seconds on average.
        assert entry["average_ms"] < 5000, entry["name"]
    print_benchmarks(loaded)


def print_benchmarks(loaded):
    """Human-readable summary (the JSON file is the artifact)."""
    print("\nbenchmark results (avg ms):")
    for entry in loaded["results"]:
        print(
            f"  {entry['category']:>12} {entry['name']:<22} "
            f"{entry['average_ms']:>8.2f}ms x{entry['iterations']}"
        )


def test_measure_counts_all_runs():
    result = measure("x", "y", 5, lambda i: i)
    assert result.iterations == 5
    assert result.total_s >= 0
