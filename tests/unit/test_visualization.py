"""Gate 21: visualization derives from events; backend stays authoritative."""

import time

from ai_ecosystem.core.events import Event, EventBus, InMemoryEventStore
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.interface import EventAdapter


def _event(kind, task_id="t1", payload=None):
    return Event(event_type=kind, task_id=task_id, payload=payload or {})


def _lifecycle(task_id="t1"):
    return [
        _event(EventType.TASK_CREATED, task_id),
        _event(EventType.STEP_READY, task_id, {"step_id": "a"}),
        _event(EventType.STEP_STARTED, task_id, {"step_id": "a"}),
        _event(EventType.TOOL_REQUESTED, task_id,
               {"tool": "filesystem.read", "call_id": "c1"}),
        _event(EventType.TOOL_STARTED, task_id,
               {"tool": "filesystem.read", "call_id": "c1"}),
        _event(EventType.TOOL_COMPLETED, task_id,
               {"tool": "filesystem.read", "call_id": "c1"}),
        _event(EventType.STEP_COMPLETED, task_id, {"step_id": "a"}),
        _event(EventType.VERIFICATION_STARTED, task_id),
        _event(EventType.VERIFICATION_PASSED, task_id),
        _event(EventType.GRAPH_COMPLETED, task_id),
    ]


def test_1_event_ingestion():
    adapter = EventAdapter()
    for event in _lifecycle():
        adapter.ingest(event)
    view = adapter.snapshot("t1")
    assert view is not None
    assert view.state == "COMPLETED"
    assert view.verification == "PASSED"


def test_2_state_transitions():
    adapter = EventAdapter()
    adapter.ingest(_event(EventType.TASK_CREATED))
    assert adapter.snapshot("t1").state == "CREATED"
    adapter.ingest(_event(EventType.STEP_STARTED, payload={"step_id": "a"}))
    assert adapter.snapshot("t1").steps["a"].state == "RUNNING"
    adapter.ingest(_event(EventType.STEP_FAILED, payload={"step_id": "a"}))
    assert adapter.snapshot("t1").state == "FAILED"


def test_3_dag_rendering_data():
    adapter = EventAdapter()
    adapter.note_step_deps("t1", "a", [], label="Read")
    adapter.note_step_deps("t1", "b", ["a"], label="Build")
    adapter.ingest(_event(EventType.STEP_COMPLETED, payload={"step_id": "a"}))
    dag = adapter.dag("t1")
    assert {n["id"] for n in dag["nodes"]} == {"a", "b"}
    assert dag["edges"] == [{"from": "a", "to": "b"}]
    assert adapter.dag("unknown") == {"nodes": [], "edges": []}


def test_4_multi_agent_state():
    adapter = EventAdapter()
    adapter.ingest(_event(EventType.AGENT_STARTED, payload={"agent_id": "r1"}))
    adapter.ingest(_event(EventType.STEP_STARTED, payload={"step_id": "s1"}))
    adapter.ingest(_event(EventType.AGENT_COMPLETED,
                          payload={"agent_id": "r1", "success": True}))
    agent = adapter.snapshot("t1").agents["r1"]
    assert agent.status == "COMPLETED"
    # Step events carry no agent attribution, so the view must NOT guess
    # which agent runs a step (previously it stamped every agent).
    assert agent.current_step == ""


def test_5_tool_activity():
    adapter = EventAdapter()
    for event in _lifecycle():
        adapter.ingest(event)
    activity = adapter.snapshot("t1").tools["c1"]
    assert activity.tool == "filesystem.read"
    assert activity.state == "COMPLETED"
    assert activity.duration_ms is not None
    dumped = activity.model_dump_json()
    assert "arguments" not in dumped and "output" not in dumped


def test_6_permission_state():
    adapter = EventAdapter()
    adapter.ingest(_event(EventType.TOOL_REQUESTED,
                          payload={"tool": "terminal.execute", "call_id": "c9"}))
    pending = adapter.snapshot("t1").permissions["c9"]
    assert pending.decided is False  # awaiting policy, clearly shown
    adapter.ingest(_event(EventType.PERMISSION_DENIED,
                          payload={"tool": "terminal.execute", "call_id": "c9",
                                   "reason": "HIGH risk"}))
    decided = adapter.snapshot("t1").permissions["c9"]
    assert decided.decided is True and decided.granted is False


def test_7_failed_task_state():
    adapter = EventAdapter()
    adapter.ingest(_event(EventType.TASK_CREATED))
    adapter.ingest(_event(EventType.STEP_FAILED, payload={"step_id": "a"}))
    view = adapter.snapshot("t1")
    assert view.state == "FAILED"
    assert view.steps["a"].state == "FAILED"


def test_8_recovery_state():
    adapter = EventAdapter()
    adapter.ingest(_event(EventType.RECOVERY_STARTED))
    adapter.ingest(_event(EventType.RECOVERY_DECIDED, payload={"action": "RETRY"}))
    view = adapter.snapshot("t1")
    assert view.state == "RECOVERING"
    assert view.recovery == "RETRY"


def test_9_reconnect_behavior():
    live = EventAdapter()
    store = InMemoryEventStore()
    for event in _lifecycle():
        live.ingest(event)
        store.append(event)
    before = live.snapshot("t1").model_dump()
    fresh = EventAdapter()  # reconnect: replay the store from scratch
    mark = fresh.ingest_store(store.list())
    assert mark == len(store.list()) - 1
    assert fresh.snapshot("t1").model_dump() == before


def test_10_event_ordering():
    adapter = EventAdapter()
    mark = adapter.ingest_store(_lifecycle())
    assert adapter.snapshot("t1").state == "COMPLETED"
    assert mark == len(_lifecycle()) - 1


def test_11_duplicate_event_handling():
    adapter = EventAdapter()
    events = _lifecycle()
    adapter.ingest_store(events)
    before = adapter.snapshot("t1").model_dump()
    adapter.ingest_store(events, last_seen=10**9)  # everything stale
    assert adapter.snapshot("t1").model_dump() == before


def test_12_stale_event_handling():
    adapter = EventAdapter()
    events = _lifecycle()
    adapter.ingest_store(events)
    adapter.ingest_store(events[:3], last_seen=10**9)
    assert adapter.snapshot("t1").state == "COMPLETED"  # not rewound


def test_model_info_via_note():
    adapter = EventAdapter()
    adapter.ingest(_event(EventType.TASK_CREATED))
    adapter.note_model("t1", "mock-7b", provider="mock")
    view = adapter.snapshot("t1")
    assert view.model == "mock-7b" and view.provider == "mock"


def test_adapter_live_subscription():
    bus = EventBus()
    adapter = EventAdapter(bus)
    bus.publish(_event(EventType.TASK_CREATED, "live-1"))
    assert adapter.snapshot("live-1").state == "CREATED"


def test_viz_perf_smoke():
    adapter = EventAdapter()
    events = _lifecycle("perf") * 20
    started = time.monotonic()
    adapter.ingest_store(events)
    adapter.dag("perf")
    elapsed = time.monotonic() - started
    print(f"\nvisualization smoke: 200 events + dag in {elapsed:.3f}s")
    assert elapsed < 5
