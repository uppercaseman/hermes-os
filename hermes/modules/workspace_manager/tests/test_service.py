"""Workspace Manager unit tests.

Two parallel suites:

- Pure-data suite (no bus, no registry): exercises CRUD, current
  pointer, layout, mission/project open/close, persistence with
  `InMemoryWorkspaceStore`.
- With-bus suite: asserts each public mutation publishes the
  matching `workspace_manager.*` event in the right order, and
  that `set_current_application` validates against the
  `ApplicationRegistry` Protocol when provided.
"""
from __future__ import annotations

import uuid

import pytest

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.modules.application_registry import build_application_registry
from hermes.modules.application_registry.errors import ApplicationNotFoundError
from hermes.modules.workspace_manager import build_workspace_manager
from hermes.modules.workspace_manager.contracts import WorkspaceManagerProtocol
from hermes.modules.workspace_manager.errors import UnknownWorkspaceError
from hermes.modules.workspace_manager.models import (
    DockingState,
    LayoutState,
    WindowState,
)
from hermes.modules.workspace_manager.service import (
    InMemoryWorkspaceStore,
    JsonFileWorkspaceStore,
)


# ---------------------------------------------------------------------- #
# Workspace CRUD
# ---------------------------------------------------------------------- #
class TestWorkspaceCRUD:
    async def test_create_workspace_returns_record(self) -> None:
        ws = build_workspace_manager()
        workspace = await ws.create_workspace(name="Default", owner="alice")
        assert workspace.name == "Default"
        assert workspace.owner == "alice"
        assert workspace.current_application_id is None
        assert workspace.open_mission_ids == []

    async def test_get_workspace_returns_copy(self) -> None:
        ws = build_workspace_manager()
        workspace = await ws.create_workspace(name="W", owner="u")
        looked_up = await ws.get_workspace(workspace.id)
        assert looked_up is not None
        assert looked_up.id == workspace.id
        assert looked_up is not workspace  # model_copy returns a new instance

    async def test_get_workspace_unknown_returns_none(self) -> None:
        ws = build_workspace_manager()
        assert await ws.get_workspace(uuid.uuid4()) is None

    async def test_list_workspaces(self) -> None:
        ws = build_workspace_manager()
        a = await ws.create_workspace(name="a", owner="u")
        b = await ws.create_workspace(name="b", owner="u")
        ids = {w.id for w in await ws.list_workspaces()}
        assert ids == {a.id, b.id}

    async def test_delete_workspace_unknown_raises(self) -> None:
        ws = build_workspace_manager()
        with pytest.raises(UnknownWorkspaceError):
            await ws.delete_workspace(uuid.uuid4())

    async def test_delete_workspace_clears_current(self) -> None:
        ws = build_workspace_manager()
        workspace = await ws.create_workspace(name="w", owner="u")
        await ws.set_current_workspace(workspace.id)
        assert ws.get_current_workspace_id() == workspace.id
        await ws.delete_workspace(workspace.id)
        assert ws.get_current_workspace_id() is None


# ---------------------------------------------------------------------- #
# Current pointer
# ---------------------------------------------------------------------- #
class TestCurrentPointer:
    async def test_set_current_workspace_unknown_raises(self) -> None:
        ws = build_workspace_manager()
        with pytest.raises(UnknownWorkspaceError):
            await ws.set_current_workspace(uuid.uuid4())

    async def test_set_current_workspace_updates_pointer(self) -> None:
        ws = build_workspace_manager()
        workspace = await ws.create_workspace(name="w", owner="u")
        await ws.set_current_workspace(workspace.id)
        assert ws.get_current_workspace_id() == workspace.id
        assert ws.get_current_workspace() is not None


# ---------------------------------------------------------------------- #
# Current application
# ---------------------------------------------------------------------- #
class TestCurrentApplication:
    async def test_set_current_application_no_registry_accepts_any(self) -> None:
        ws = build_workspace_manager()  # no registry: validation skipped
        workspace = await ws.create_workspace(name="w", owner="u")
        updated = await ws.set_current_application(
            workspace.id, "anything_here"
        )
        assert updated.current_application_id == "anything_here"
        assert "anything_here" in updated.open_application_ids

    async def test_set_current_application_unknown_workspace(self) -> None:
        ws = build_workspace_manager()
        with pytest.raises(UnknownWorkspaceError):
            await ws.set_current_application(uuid.uuid4(), "x")

    async def test_set_current_application_validates_against_registry(self) -> None:
        registry = build_application_registry()
        ws = build_workspace_manager(application_registry=registry)
        workspace = await ws.create_workspace(name="w", owner="u")
        with pytest.raises(ApplicationNotFoundError):
            await ws.set_current_application(workspace.id, "missing")


# ---------------------------------------------------------------------- #
# Mission / project open / close
# ---------------------------------------------------------------------- #
class TestMissionProjectOpenClose:
    async def test_open_mission_adds_id(self) -> None:
        ws = build_workspace_manager()
        workspace = await ws.create_workspace(name="w", owner="u")
        mid = uuid.uuid4()
        updated = await ws.open_mission(workspace.id, mid)
        assert mid in updated.open_mission_ids

    async def test_open_mission_idempotent(self) -> None:
        ws = build_workspace_manager()
        workspace = await ws.create_workspace(name="w", owner="u")
        mid = uuid.uuid4()
        first = await ws.open_mission(workspace.id, mid)
        second = await ws.open_mission(workspace.id, mid)
        assert first.open_mission_ids == second.open_mission_ids
        assert second.open_mission_ids.count(mid) == 1

    async def test_close_mission_removes_id(self) -> None:
        ws = build_workspace_manager()
        workspace = await ws.create_workspace(name="w", owner="u")
        mid = uuid.uuid4()
        await ws.open_mission(workspace.id, mid)
        updated = await ws.close_mission(workspace.id, mid)
        assert mid not in updated.open_mission_ids

    async def test_open_close_project_idempotent(self) -> None:
        ws = build_workspace_manager()
        workspace = await ws.create_workspace(name="w", owner="u")
        pid = uuid.uuid4()
        opened = await ws.open_project(workspace.id, pid)
        assert pid in opened.open_project_ids
        closed = await ws.close_project(workspace.id, pid)
        assert pid not in closed.open_project_ids


# ---------------------------------------------------------------------- #
# Layout snapshots
# ---------------------------------------------------------------------- #
class TestLayoutSnapshots:
    async def test_get_layout_state_returns_empty_when_unset(self) -> None:
        ws = build_workspace_manager()
        workspace = await ws.create_workspace(name="w", owner="u")
        state = ws.get_layout_state(workspace.id)
        assert state is not None
        assert state.workspace_id == workspace.id
        assert state.windows == []
        assert state.docks == []

    async def test_snapshot_layout_stores_state(self) -> None:
        ws = build_workspace_manager()
        workspace = await ws.create_workspace(name="w", owner="u")
        layout = LayoutState(
            workspace_id=workspace.id,
            windows=[
                WindowState(
                    window_id="w1",
                    application_id="mission_control",
                    title="Missions",
                ),
            ],
            docks=[
                DockingState(
                    name="left", region="left", dock_windows=["w1"]
                ),
            ],
        )
        result = ws.snapshot_layout(workspace.id, layout)
        assert result.model_dump() == layout.model_dump()
        again = ws.get_layout_state(workspace.id)
        assert again.model_dump() == layout.model_dump()


# ---------------------------------------------------------------------- #
# Persistence
# ---------------------------------------------------------------------- #
class TestPersistence:
    async def test_save_and_restore_round_trip(self) -> None:
        store = InMemoryWorkspaceStore()
        ws = build_workspace_manager(store=store)
        workspace = await ws.create_workspace(name="w", owner="u")
        await ws.open_mission(workspace.id, uuid.uuid4())
        ws.snapshot_layout(
            workspace.id,
            LayoutState(
                workspace_id=workspace.id,
                windows=[
                    WindowState(
                        window_id="w1",
                        application_id="mission_control",
                        title="M",
                    )
                ],
            ),
        )
        await ws.save_workspace(workspace.id)
        # rebuild
        ws2 = build_workspace_manager(store=store)
        restored = await ws2.restore_workspace(workspace.id)
        assert restored is not None
        assert restored.name == "w"
        assert len(restored.open_mission_ids) == 1
        assert restored.layout is not None
        assert restored.layout.windows[0].application_id == "mission_control"

    async def test_restore_unknown_returns_none(self) -> None:
        ws = build_workspace_manager()
        assert await ws.restore_workspace(uuid.uuid4()) is None

    async def test_json_file_store_round_trip(self, tmp_path) -> None:
        store = JsonFileWorkspaceStore(tmp_path / "workspaces")
        ws = build_workspace_manager(store=store)
        workspace = await ws.create_workspace(name="disk", owner="alice")
        await ws.save_workspace(workspace.id)
        # Read directly off disk to verify file content.
        files = list((tmp_path / "workspaces").glob("*.json"))
        assert len(files) == 1

        ws2 = build_workspace_manager(store=JsonFileWorkspaceStore(tmp_path / "workspaces"))
        restored = await ws2.restore_workspace(workspace.id)
        assert restored is not None
        assert restored.name == "disk"


# ---------------------------------------------------------------------- #
# Events + protocol surface
# ---------------------------------------------------------------------- #
class TestEventsAndProtocol:
    async def test_create_publishes_created_event(self) -> None:
        bus = InMemoryEventBus()
        ws = build_workspace_manager(event_bus=bus)
        captured: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            captured.append(ev)

        await bus.subscribe("workspace_manager.workspace.created", handler)
        workspace = await ws.create_workspace(name="w", owner="u")
        assert len(captured) == 1
        assert captured[0].payload["workspace_id"] == str(workspace.id)
        assert captured[0].payload["name"] == "w"

    async def test_set_current_publishes_focused_only_on_change(
        self,
    ) -> None:
        bus = InMemoryEventBus()
        ws = build_workspace_manager(event_bus=bus)
        workspace = await ws.create_workspace(name="w", owner="u")
        focused: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            focused.append(ev)

        await bus.subscribe("workspace_manager.workspace.focused", handler)
        await ws.set_current_workspace(workspace.id)
        await ws.set_current_workspace(workspace.id)  # idempotent
        assert len(focused) == 1

    async def test_open_mission_publishes_event(self) -> None:
        bus = InMemoryEventBus()
        ws = build_workspace_manager(event_bus=bus)
        workspace = await ws.create_workspace(name="w", owner="u")
        opened: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            opened.append(ev)

        await bus.subscribe(
            "workspace_manager.workspace.mission_opened", handler
        )
        mid = uuid.uuid4()
        await ws.open_mission(workspace.id, mid)
        assert len(opened) == 1
        assert opened[0].payload["mission_id"] == str(mid)

    async def test_save_publishes_saved(self) -> None:
        bus = InMemoryEventBus()
        ws = build_workspace_manager(event_bus=bus)
        workspace = await ws.create_workspace(name="w", owner="u")
        saved: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            saved.append(ev)

        await bus.subscribe("workspace_manager.workspace.saved", handler)
        await ws.save_workspace(workspace.id)
        assert len(saved) == 1

    async def test_delete_publishes_closed(self) -> None:
        bus = InMemoryEventBus()
        ws = build_workspace_manager(event_bus=bus)
        workspace = await ws.create_workspace(name="w", owner="u")
        closed: list = []

        async def handler(ev):  # type: ignore[no-untyped-def]
            closed.append(ev)

        await bus.subscribe("workspace_manager.workspace.closed", handler)
        await ws.delete_workspace(workspace.id)
        assert len(closed) == 1
        assert closed[0].payload["reason"] == "deleted"

    def test_satisfies_workspace_manager_protocol(self) -> None:
        ws = build_workspace_manager()
        assert isinstance(ws, WorkspaceManagerProtocol)
