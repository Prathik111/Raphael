"""Gate 22: typed nodes, layout, validation, persistence, XSS rejection."""

import time

import pytest

from ai_ecosystem.core.errors import DomainValidationError, ResourceNotFoundError
from ai_ecosystem.core.events import Event, EventBus
from ai_ecosystem.core.models.enums import EventType
from ai_ecosystem.core.persistence import Database
from ai_ecosystem.interface import (
    DataRef,
    NodeLayout,
    NodeState,
    NodeType,
    SqliteWorkspaceRepository,
    WorkspaceManager,
)


@pytest.fixture()
def manager():
    db = Database(":memory:")
    db.migrate()
    bus = EventBus()
    yield WorkspaceManager(SqliteWorkspaceRepository(db), bus), bus
    db.close()


def test_1_node_creation(manager):
    workspaces, _ = manager
    workspace = workspaces.create("p1")
    node = workspaces.add_node(workspace.id, NodeType.PROSE, {"title": "Report"})
    assert node.node_type is NodeType.PROSE
    assert node.props == {"title": "Report"}
    assert node.state is NodeState.LIVE


def test_2_node_update(manager):
    workspaces, _ = manager
    workspace = workspaces.create()
    node = workspaces.add_node(workspace.id, NodeType.CODE, {"language": "python"})
    updated = workspaces.update_node(workspace.id, node.id, {"language": "python",
                                                             "lines": 10})
    assert updated.props["lines"] == 10


def test_3_node_removal(manager):
    workspaces, _ = manager
    workspace = workspaces.create()
    node = workspaces.add_node(workspace.id, NodeType.PROSE, {})
    assert workspaces.remove_node(workspace.id, node.id) is True
    assert workspaces.remove_node(workspace.id, node.id) is False
    with pytest.raises(ResourceNotFoundError):
        workspaces.update_node(workspace.id, node.id, {})


def test_4_layout(manager):
    workspaces, _ = manager
    workspace = workspaces.create()
    node = workspaces.add_node(workspace.id, NodeType.CHART, {})
    layout = workspaces.move_node(workspace.id, node.id, 2, 3, width=6)
    assert (layout.x, layout.y, layout.width) == (2, 3, 6)
    workspaces.focus(workspace.id, node.id)
    assert workspaces.get(workspace.id).focused_node == node.id


def test_5_grouping(manager):
    workspaces, _ = manager
    workspace = workspaces.create()
    container = workspaces.add_node(workspace.id, NodeType.CONTAINER, {})
    child = workspaces.add_node(workspace.id, NodeType.PROSE, {})
    workspaces.group_nodes(workspace.id, container.id, [child.id])
    assert workspaces.get(workspace.id).layout[child.id].group == container.id


def test_6_collapse_expand(manager):
    workspaces, _ = manager
    workspace = workspaces.create()
    container = workspaces.add_node(workspace.id, NodeType.CONTAINER, {})
    child = workspaces.add_node(workspace.id, NodeType.PROSE, {})
    workspaces.group_nodes(workspace.id, container.id, [child.id])
    assert len(workspaces.visible_nodes(workspace.id)) == 2
    workspaces.set_collapsed(workspace.id, container.id, True)
    visible = workspaces.visible_nodes(workspace.id)
    assert [n.id for n in visible] == [container.id]
    workspaces.set_collapsed(workspace.id, container.id, False)
    assert len(workspaces.visible_nodes(workspace.id)) == 2


def test_7_persistence_and_8_restart(tmp_path):
    path = str(tmp_path / "ws.db")
    first = Database(path)
    first.migrate()
    manager = WorkspaceManager(SqliteWorkspaceRepository(first))
    workspace = manager.create("p1")
    manager.add_node(workspace.id, NodeType.PROSE, {"title": "Keep"})
    workspace_id = workspace.id
    first.close()
    second = Database(path)
    second.migrate()
    try:
        restored = WorkspaceManager(SqliteWorkspaceRepository(second)).get(workspace_id)
    finally:
        second.close()
    assert restored.project_scope == "p1"
    assert len(restored.nodes) == 1


def test_9_malformed_node_rejection(manager):
    workspaces, _ = manager
    workspace = workspaces.create()
    with pytest.raises(DomainValidationError):
        workspaces.apply_update(workspace.id, {"type": "nonsense"})
    with pytest.raises(DomainValidationError):
        workspaces.apply_update(workspace.id, {"type": "prose", "props": [1, 2]})
    with pytest.raises(DomainValidationError):
        workspaces.apply_update(workspace.id, "not a mapping")


def test_10_arbitrary_javascript_rejection(manager):
    workspaces, _ = manager
    workspace = workspaces.create()
    attacks = [
        {"type": "prose", "props": {"html": "<script>eval(document.cookie)</script>"}},
        {"type": "code", "props": {"source": "x = eval(user_input)"}},
        {"type": "chart", "props": {"url": "javascript:alert(1)"}},
        {"type": "prose", "props": {"dangerouslySetInnerHTML": {"__html": "x"}}},
        {"type": "prose", "props": {"onClick": "doEvil()"}},
        {"type": "prose", "props": {"__proto__": {"polluted": True}}},
        {"type": "diagram", "props": {"nodes": [{"label": "new Function('x')"}]}},
    ]
    for attack in attacks:
        with pytest.raises(DomainValidationError):
            workspaces.apply_update(workspace.id, attack)
    assert workspaces.get(workspace.id).nodes == {}


def test_11_oversized_payload_rejection(manager):
    workspaces, _ = manager
    workspace = workspaces.create()
    with pytest.raises(DomainValidationError, match="exceed"):
        workspaces.apply_update(workspace.id, {"type": "prose",
                                               "props": {"blob": "x" * 100_000}})


def test_12_model_generated_update_validated(manager):
    workspaces, _ = manager
    workspace = workspaces.create("research")
    # Shape a model would produce via structured output:
    update = {"type": "prose", "props": {"title": "Findings", "body": "watts"},
              "salience": 0.9, "affordances": ["expand", "inspect"],
              "data_ref": {"kind": "research", "ref_id": "r1"}}
    node = workspaces.apply_update(workspace.id, update)
    assert node.props["title"] == "Findings"
    assert node.data_ref == DataRef(kind="research", ref_id="r1")
    assert node.salience == 0.9
    with pytest.raises(DomainValidationError):
        workspaces.apply_update(workspace.id, {"type": "prose", "props": {},
                                               "affordances": ["execute"]})


def test_13_project_scoping(manager):
    workspaces, _ = manager
    workspaces.create("p1")
    workspaces.create("p2")
    workspaces.create("p1")
    assert len(workspaces.list("p1")) == 2
    assert len(workspaces.list("p2")) == 1
    assert len(workspaces.list()) == 3


def test_14_multiple_simultaneous_workspaces(manager):
    workspaces, _ = manager
    first = workspaces.create("p1")
    second = workspaces.create("p1")
    workspaces.add_node(first.id, NodeType.PROSE, {"n": 1})
    workspaces.add_node(second.id, NodeType.PROSE, {"n": 2})
    assert workspaces.get(first.id).nodes != workspaces.get(second.id).nodes


def test_frozen_nodes_reject_updates(manager):
    workspaces, _ = manager
    workspace = workspaces.create()
    node = workspaces.add_node(workspace.id, NodeType.PROSE, {"v": 1})
    workspaces.update_node(workspace.id, node.id, state=NodeState.FROZEN)
    with pytest.raises(DomainValidationError, match="FROZEN"):
        workspaces.update_node(workspace.id, node.id, {"v": 2})
    assert workspaces.get(workspace.id).nodes[node.id].props == {"v": 1}


def test_workspace_events(manager):
    workspaces, bus = manager
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    workspace = workspaces.create()
    node = workspaces.add_node(workspace.id, NodeType.PROSE, {})
    workspaces.update_node(workspace.id, node.id, {"v": 1})
    kinds = [e.event_type for e in seen]
    assert kinds.count(EventType.WORKSPACE_UPDATED) == 3
    assert all(e.payload["workspace_id"] == workspace.id for e in seen)


def test_workspace_perf_smoke(manager):
    workspaces, _ = manager
    workspace = workspaces.create()
    started = time.monotonic()
    for index in range(100):
        workspaces.add_node(workspace.id, NodeType.PROSE, {"n": index})
    elapsed = time.monotonic() - started
    print(f"\nworkspace smoke: 100 node updates in {elapsed:.2f}s")
    assert elapsed < 5
