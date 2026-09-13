"""Typed dynamic workspace: nodes, layout, validation (Gate 22).

The workspace is a presentation layer over runtime-owned data. Nodes
carry small props or DataRefs (never large blobs); every agent- or
model-shaped update passes the same schema validation, which rejects
anything resembling executable frontend code (script tags, javascript:
URLs, eval(), framework escape hatches, prototype pollution keys).
"""

from __future__ import annotations

import builtins
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ai_ecosystem.core.errors.exceptions import DomainValidationError
from ai_ecosystem.core.events.bus import Event, EventBus
from ai_ecosystem.core.models.base import Entity
from ai_ecosystem.core.models.enums import EventType

MAX_PROP_BYTES = 64_000

_FORBIDDEN_KEYS = ("__proto__", "constructor", "prototype", "dangerouslySetInnerHTML", "__html")

_FORBIDDEN_SNIPPETS = (
    "<script",
    "javascript:",
    "vbscript:",
    "data:text/html",
    "eval(",
    "new function",
    "settimeout(",
    "setinterval(",
    "expression(",
    "<img",
    "<svg",
    "<iframe",
    "<object",
    "<embed",
    "<link",
    "<meta",
    "<base",
    "<form",
    "<input",
    "<button",
    "<select",
    "<textarea",
    "<keygen",
    "<marquee",
    "onerror=",
    "onload=",
)


def _is_handler_key(key: str) -> bool:
    """Any on* event-handler attribute, in any letter case."""
    lowered = key.lower()
    return lowered.startswith("on") and len(lowered) > 2 and lowered[2:].isalpha()


class NodeType(str, Enum):
    """Allowed node kinds (closed vocabulary)."""

    CHART = "chart"
    PROSE = "prose"
    DIAGRAM = "diagram"
    CODE = "code"
    MEDIA = "media"
    CONTAINER = "container"
    PORTAL = "portal"
    TABLE = "table"


class NodeState(str, Enum):
    """Lifecycle with real behavior behind each state."""

    LIVE = "LIVE"
    FROZEN = "FROZEN"
    COLLAPSED = "COLLAPSED"
    DORMANT = "DORMANT"
    GHOST = "GHOST"


_ALLOWED_AFFORDANCES = {
    NodeType.CHART: {"zoom", "filter", "expand", "collapse", "refresh", "inspect"},
    NodeType.PROSE: {"expand", "collapse", "refresh", "inspect"},
    NodeType.DIAGRAM: {"zoom", "expand", "collapse", "refresh", "inspect"},
    NodeType.CODE: {"expand", "collapse", "open", "inspect"},
    NodeType.MEDIA: {"expand", "collapse", "open", "inspect"},
    NodeType.CONTAINER: {"expand", "collapse", "open", "inspect"},
    NodeType.PORTAL: {"open", "refresh", "inspect"},
    NodeType.TABLE: {"filter", "expand", "collapse", "refresh", "inspect"},
}


class DataRef(BaseModel):
    """Pointer to runtime-owned data (kind + id, never the blob)."""

    kind: str = ""
    ref_id: str = ""


class WorkspaceNode(Entity):
    """One typed, stateful presentation node."""

    node_type: NodeType = NodeType.PROSE
    props: dict[str, Any] = Field(default_factory=dict)
    data_ref: DataRef | None = None
    salience: float = 0.5
    state: NodeState = NodeState.LIVE
    affordances: list[str] = Field(default_factory=list)


class NodeLayout(BaseModel):
    """Positioning of one node (grid units; group or empty)."""

    x: int = 0
    y: int = 0
    width: int = 4
    height: int = 3
    group: str = ""


class Workspace(Entity):
    """A scoped collection of nodes plus their layout."""

    project_scope: str = ""
    nodes: dict[str, WorkspaceNode] = Field(default_factory=dict)
    layout: dict[str, NodeLayout] = Field(default_factory=dict)
    focused_node: str = ""


def validate_props(node_type: NodeType, props: Any) -> dict[str, Any]:
    """Reject malformed props and anything executable (raises on violation)."""
    if not isinstance(props, dict):
        raise DomainValidationError("node props must be a mapping")
    try:
        size = len(json.dumps(props).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"node props must be JSON data: {exc}") from exc
    if size > MAX_PROP_BYTES:
        raise DomainValidationError(f"node props {size} bytes exceed {MAX_PROP_BYTES} limit")
    for key, value in props.items():
        lowered = str(key).lower()
        if lowered in _FORBIDDEN_KEYS:
            raise DomainValidationError(f"forbidden prop key {key!r}")
        raw = str(key)
        if _is_handler_key(raw):
            raise DomainValidationError(f"event-handler prop {key!r} forbidden")
        _scan_value(value)
    return dict(props)


def _scan_value(value: Any) -> None:
    if isinstance(value, str):
        lowered = value.lower()
        for snippet in _FORBIDDEN_SNIPPETS:
            if snippet in lowered:
                raise DomainValidationError(f"executable content rejected ({snippet!r})")
    elif isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN_KEYS or _is_handler_key(str(key)):
                raise DomainValidationError(f"forbidden nested key {key!r}")
            _scan_value(item)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            _scan_value(item)


def validate_affordances(node_type: NodeType, affordances: list[str]) -> list[str]:
    """Affordances must belong to the node's allow-list."""
    allowed = _ALLOWED_AFFORDANCES[node_type]
    unknown = [name for name in affordances if name not in allowed]
    if unknown:
        raise DomainValidationError(f"affordances {unknown} not allowed for {node_type.value}")
    return list(affordances)


class WorkspaceManager:
    """CRUD + layout + validated agent updates over persisted workspaces."""

    def __init__(self, repository: Any, bus: EventBus | None = None) -> None:
        self._repo = repository
        self._bus = bus

    def create(self, project_scope: str = "") -> Workspace:
        workspace = self._repo.create(Workspace(project_scope=project_scope))
        self._emit(workspace.id, {"created": True})
        return workspace

    def get(self, workspace_id: str) -> Workspace:
        from ai_ecosystem.core.errors.exceptions import ResourceNotFoundError

        workspace = self._repo.get(workspace_id)
        if workspace is None:
            raise ResourceNotFoundError("Workspace", workspace_id)
        return workspace

    def list(self, project_scope: str = "") -> builtins.list[Workspace]:
        all_workspaces = self._repo.list()
        if project_scope:
            return [w for w in all_workspaces if w.project_scope == project_scope]
        return all_workspaces

    def add_node(
        self,
        workspace_id: str,
        node_type: NodeType,
        props: dict | None = None,
        data_ref: DataRef | None = None,
        salience: float = 0.5,
        affordances: builtins.list[str] | None = None,
    ) -> WorkspaceNode:
        workspace = self.get(workspace_id)
        node = WorkspaceNode(
            node_type=node_type,
            props=validate_props(node_type, props or {}),
            data_ref=data_ref,
            salience=min(1.0, max(0.0, salience)),
            affordances=validate_affordances(node_type, affordances or []),
        )
        workspace.nodes[node.id] = node
        workspace.layout[node.id] = NodeLayout()
        workspace.touch()
        self._repo.update(workspace)
        self._emit(workspace_id, {"node_id": node.id, "type": node_type.value})
        return node

    def update_node(
        self,
        workspace_id: str,
        node_id: str,
        props: dict | None = None,
        state: NodeState | None = None,
    ) -> WorkspaceNode:
        workspace = self.get(workspace_id)
        node = self._need(workspace, node_id)
        if node.state is NodeState.FROZEN:
            raise DomainValidationError(f"node {node_id!r} is FROZEN")
        if props is not None:
            node.props = validate_props(node.node_type, props)
        if state is not None:
            node.state = state
        node.touch()
        workspace.touch()
        self._repo.update(workspace)
        self._emit(workspace_id, {"node_id": node_id, "updated": True})
        return node

    def remove_node(self, workspace_id: str, node_id: str) -> bool:
        workspace = self.get(workspace_id)
        if node_id not in workspace.nodes:
            return False
        del workspace.nodes[node_id]
        workspace.layout.pop(node_id, None)
        if workspace.focused_node == node_id:
            workspace.focused_node = ""
        workspace.touch()
        self._repo.update(workspace)
        self._emit(workspace_id, {"node_id": node_id, "removed": True})
        return True

    def move_node(
        self, workspace_id: str, node_id: str, x: int, y: int, width: int = 0, height: int = 0
    ) -> NodeLayout:
        for label, value in (("x", x), ("y", y), ("width", width), ("height", height)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise DomainValidationError(f"layout {label} must be an integer")
            if value < 0:
                raise DomainValidationError(f"layout {label} must be >= 0")
        workspace = self.get(workspace_id)
        self._need(workspace, node_id)
        layout = workspace.layout.get(node_id, NodeLayout())
        layout.x, layout.y = x, y
        if width > 0:
            layout.width = width
        if height > 0:
            layout.height = height
        workspace.layout[node_id] = layout
        workspace.touch()
        self._repo.update(workspace)
        return layout

    def group_nodes(self, workspace_id: str, group: str, node_ids: builtins.list[str]) -> None:
        workspace = self.get(workspace_id)
        for node_id in node_ids:
            self._need(workspace, node_id)
            layout = workspace.layout.get(node_id, NodeLayout())
            layout.group = group
            workspace.layout[node_id] = layout
        workspace.touch()
        self._repo.update(workspace)

    def set_collapsed(self, workspace_id: str, node_id: str, collapsed: bool) -> WorkspaceNode:
        node = self.get(workspace_id).nodes.get(node_id)
        if node is None:
            from ai_ecosystem.core.errors.exceptions import ResourceNotFoundError

            raise ResourceNotFoundError("WorkspaceNode", node_id)
        return self.update_node(
            workspace_id, node_id, state=NodeState.COLLAPSED if collapsed else NodeState.LIVE
        )

    def focus(self, workspace_id: str, node_id: str) -> None:
        workspace = self.get(workspace_id)
        self._need(workspace, node_id)
        workspace.focused_node = node_id
        workspace.touch()
        self._repo.update(workspace)

    def visible_nodes(self, workspace_id: str) -> builtins.list[WorkspaceNode]:
        workspace = self.get(workspace_id)
        collapsed_ids = {
            nid for nid, node in workspace.nodes.items() if node.state is NodeState.COLLAPSED
        }
        return [
            node
            for nid, node in workspace.nodes.items()
            if workspace.layout.get(nid, NodeLayout()).group not in collapsed_ids
        ]

    def apply_update(self, workspace_id: str, update: dict) -> WorkspaceNode:
        if not isinstance(update, dict):
            raise DomainValidationError("workspace update must be a mapping")
        try:
            node_type = NodeType(str(update.get("type", "")))
        except ValueError:
            raise DomainValidationError(f"unknown node type {update.get('type')!r}") from None
        ref = update.get("data_ref")
        data_ref = None
        if ref is not None:
            if not isinstance(ref, dict):
                raise DomainValidationError("data_ref must be a mapping")
            data_ref = DataRef(kind=str(ref.get("kind", "")), ref_id=str(ref.get("ref_id", "")))
            validate_props(node_type, {"kind": data_ref.kind, "ref_id": data_ref.ref_id})
        try:
            salience = float(update.get("salience", 0.5))
        except (TypeError, ValueError):
            raise DomainValidationError("salience must be a number") from None
        if not 0.0 <= salience <= 1.0:
            raise DomainValidationError("salience must be within [0, 1]")
        raw_affordances = update.get("affordances", [])
        if not isinstance(raw_affordances, list | tuple):
            raise DomainValidationError("affordances must be a list")
        if any(not isinstance(name, str) for name in raw_affordances):
            raise DomainValidationError("affordances must be strings")
        return self.add_node(
            workspace_id,
            node_type,
            props=update.get("props", {}),
            data_ref=data_ref,
            salience=salience,
            affordances=list(raw_affordances),
        )

    @staticmethod
    def _need(workspace: Workspace, node_id: str) -> WorkspaceNode:
        from ai_ecosystem.core.errors.exceptions import ResourceNotFoundError

        node = workspace.nodes.get(node_id)
        if node is None:
            raise ResourceNotFoundError("WorkspaceNode", node_id)
        return node

    def _emit(self, workspace_id: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(
                    event_type=EventType.WORKSPACE_UPDATED,
                    task_id="",
                    payload={"workspace_id": workspace_id, **payload},
                )
            )


class SqliteWorkspaceRepository:
    """Persisted workspaces in the existing database (no new database)."""

    def __init__(self, db: Any) -> None:
        from ai_ecosystem.core.persistence.sqlite import _SnapshotTable

        self._t = _SnapshotTable(db, "workspaces", Workspace)

    def create(self, item: Workspace) -> Workspace:
        return self._t.create(item)

    def get(self, item_id: str) -> Workspace | None:
        return self._t.get(item_id)

    def update(self, item: Workspace) -> Workspace:
        return self._t.update(item)

    def delete(self, item_id: str) -> bool:
        return self._t.delete(item_id)

    def list(self) -> list[Workspace]:
        return self._t.list()
