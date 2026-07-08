"""Workspace Manager service.

The Manager owns an in-memory dict of `Workspace` records plus a
`current_workspace_id` pointer. Every mutation publishes the
matching `workspace_manager.*` event. Persistence is delegated to
a pluggable `WorkspaceStore` -- tests pass `InMemoryWorkspaceStore`,
production callers pass `JsonFileStore` (defined below).

Key properties:

- **Pure workspace data.** The Manager does not call Commander,
  the Mission System, or any other workspace module's runtime.
  It only validates app ids against the `ApplicationRegistry`
  Protocol.
- **Idempotent focused / open.** `set_current_workspace` publishes
  `workspace.focused` only when the pointer actually changes; same
  for `open_mission` / `close_mission`.
- **No cache.** The Manager does not cache layout states; it
  always derives them from the underlying `Workspace`.
- **Restore semantics.** `restore_workspace(workspace_id)` reads
  from the store, re-installs the record in memory (or replaces
  the in-memory copy if one exists), and returns the restored
  record. The caller is responsible for event publication
  bookkeeping if it cares about `workspace.opened`.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.application_registry.contracts import ApplicationSource
from hermes.modules.workspace_manager import events as evt
from hermes.modules.workspace_manager.contracts import WorkspaceStore
from hermes.modules.workspace_manager.errors import UnknownWorkspaceError
from hermes.modules.workspace_manager.models import (
    LayoutState,
    Workspace,
)

SOURCE_MODULE = "workspace_manager"


# ---------------------------------------------------------------------- #
# Default stores
# ---------------------------------------------------------------------- #
class InMemoryWorkspaceStore:
    """Default in-memory `WorkspaceStore` used by tests and ephemeral runs.
    Lives in the same process; nothing persists across restarts."""

    def __init__(self) -> None:
        self._records: dict[uuid.UUID, Workspace] = {}

    async def save(self, workspace: Workspace) -> None:
        self._records[workspace.id] = workspace.model_copy(deep=True)

    async def load(self, workspace_id: uuid.UUID) -> Workspace | None:
        record = self._records.get(workspace_id)
        if record is None:
            return None
        return record.model_copy(deep=True)

    async def list_ids(self) -> list[uuid.UUID]:
        return list(self._records.keys())


class JsonFileWorkspaceStore:
    """On-disk `WorkspaceStore` that writes one JSON file per workspace
    under `directory/<uuid>.json`. Used by the future desktop UI's
    startup path; tests use `InMemoryWorkspaceStore` so no disk I/O
    happens in CI."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)

    def _path_for(self, workspace_id: uuid.UUID) -> Path:
        return self._directory / f"{workspace_id}.json"

    async def save(self, workspace: Workspace) -> None:
        path = self._path_for(workspace.id)
        path.write_text(workspace.model_dump_json(indent=2), encoding="utf-8")

    async def load(self, workspace_id: uuid.UUID) -> Workspace | None:
        path = self._path_for(workspace_id)
        if not path.exists():
            return None
        return Workspace.model_validate_json(path.read_text(encoding="utf-8"))

    async def list_ids(self) -> list[uuid.UUID]:
        ids: list[uuid.UUID] = []
        for path in self._directory.glob("*.json"):
            try:
                ids.append(uuid.UUID(path.stem))
            except ValueError:
                continue
        return ids


# ---------------------------------------------------------------------- #
# Manager
# ---------------------------------------------------------------------- #
class WorkspaceManager:
    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        application_registry: ApplicationSource | None = None,
        store: WorkspaceStore,
    ) -> None:
        self._bus = event_bus
        self._registry = application_registry
        self._store = store
        self._workspaces: dict[uuid.UUID, Workspace] = {}
        self._current_id: uuid.UUID | None = None

    # ------------------------------------------------------------------ #
    # Workspace CRUD
    # ------------------------------------------------------------------ #
    async def create_workspace(
        self, *, name: str, owner: str, description: str = ""
    ) -> Workspace:
        workspace = Workspace(name=name, owner=owner, description=description)
        self._workspaces[workspace.id] = workspace
        await self._publish(
            evt.WORKSPACE_CREATED,
            {
                "workspace_id": str(workspace.id),
                "name": name,
                "owner": owner,
            },
        )
        return workspace.model_copy()

    async def get_workspace(self, workspace_id: uuid.UUID) -> Workspace | None:
        record = self._workspaces.get(workspace_id)
        return record.model_copy() if record is not None else None

    async def list_workspaces(self) -> list[Workspace]:
        return [w.model_copy() for w in self._workspaces.values()]

    async def delete_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        try:
            record = self._workspaces.pop(workspace_id)
        except KeyError as exc:
            raise UnknownWorkspaceError(workspace_id) from exc
        if self._current_id == workspace_id:
            self._current_id = None
        await self._publish(
            evt.WORKSPACE_CLOSED,
            {
                "workspace_id": str(workspace_id),
                "name": record.name,
                "reason": "deleted",
            },
        )
        return record.model_copy()

    # ------------------------------------------------------------------ #
    # Current pointer
    # ------------------------------------------------------------------ #
    async def set_current_workspace(
        self, workspace_id: uuid.UUID
    ) -> Workspace:
        if workspace_id not in self._workspaces:
            raise UnknownWorkspaceError(workspace_id)
        previous = self._current_id
        self._current_id = workspace_id
        record = self._workspaces[workspace_id]
        if previous != workspace_id:
            await self._publish(
                evt.WORKSPACE_FOCUSED,
                {
                    "workspace_id": str(workspace_id),
                    "name": record.name,
                    "previous_workspace_id": (
                        str(previous) if previous is not None else None
                    ),
                },
            )
        return record.model_copy()

    def get_current_workspace(self) -> Workspace | None:
        if self._current_id is None:
            return None
        record = self._workspaces.get(self._current_id)
        return record.model_copy() if record is not None else None

    def get_current_workspace_id(self) -> uuid.UUID | None:
        return self._current_id

    # ------------------------------------------------------------------ #
    # Current application
    # ------------------------------------------------------------------ #
    async def set_current_application(
        self, workspace_id: uuid.UUID, application_id: str
    ) -> Workspace:
        if workspace_id not in self._workspaces:
            raise UnknownWorkspaceError(workspace_id)
        if self._registry is not None and not self._registry.has_application(
            application_id
        ):
            from hermes.modules.application_registry.errors import (
                ApplicationNotFoundError,
            )

            raise ApplicationNotFoundError(application_id)
        record = self._workspaces[workspace_id]
        updated = record.model_copy(
            update={
                "current_application_id": application_id,
                "open_application_ids": sorted(
                    set(record.open_application_ids) | {application_id}
                ),
                "updated_at": _now(),
            }
        )
        self._workspaces[workspace_id] = updated
        await self._publish(
            evt.LAYOUT_CHANGED,
            {
                "workspace_id": str(workspace_id),
                "kind": "current_application_changed",
                "application_id": application_id,
            },
        )
        return updated.model_copy()

    # ------------------------------------------------------------------ #
    # Open / close missions
    # ------------------------------------------------------------------ #
    async def open_mission(
        self, workspace_id: uuid.UUID, mission_id: uuid.UUID
    ) -> Workspace:
        if workspace_id not in self._workspaces:
            raise UnknownWorkspaceError(workspace_id)
        record = self._workspaces[workspace_id]
        if mission_id in record.open_mission_ids:
            return record.model_copy()
        updated = record.model_copy(
            update={
                "open_mission_ids": [*record.open_mission_ids, mission_id],
                "updated_at": _now(),
            }
        )
        self._workspaces[workspace_id] = updated
        await self._publish(
            evt.WORKSPACE_MISSION_OPENED,
            {
                "workspace_id": str(workspace_id),
                "mission_id": str(mission_id),
            },
        )
        return updated.model_copy()

    async def close_mission(
        self, workspace_id: uuid.UUID, mission_id: uuid.UUID
    ) -> Workspace:
        if workspace_id not in self._workspaces:
            raise UnknownWorkspaceError(workspace_id)
        record = self._workspaces[workspace_id]
        if mission_id not in record.open_mission_ids:
            return record.model_copy()
        updated = record.model_copy(
            update={
                "open_mission_ids": [
                    m for m in record.open_mission_ids if m != mission_id
                ],
                "updated_at": _now(),
            }
        )
        self._workspaces[workspace_id] = updated
        await self._publish(
            evt.WORKSPACE_MISSION_CLOSED,
            {
                "workspace_id": str(workspace_id),
                "mission_id": str(mission_id),
            },
        )
        return updated.model_copy()

    async def open_project(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> Workspace:
        if workspace_id not in self._workspaces:
            raise UnknownWorkspaceError(workspace_id)
        record = self._workspaces[workspace_id]
        if project_id in record.open_project_ids:
            return record.model_copy()
        updated = record.model_copy(
            update={
                "open_project_ids": [*record.open_project_ids, project_id],
                "updated_at": _now(),
            }
        )
        self._workspaces[workspace_id] = updated
        return updated.model_copy()

    async def close_project(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> Workspace:
        if workspace_id not in self._workspaces:
            raise UnknownWorkspaceError(workspace_id)
        record = self._workspaces[workspace_id]
        if project_id not in record.open_project_ids:
            return record.model_copy()
        updated = record.model_copy(
            update={
                "open_project_ids": [
                    p for p in record.open_project_ids if p != project_id
                ],
                "updated_at": _now(),
            }
        )
        self._workspaces[workspace_id] = updated
        return updated.model_copy()

    # ------------------------------------------------------------------ #
    # Layout snapshot
    # ------------------------------------------------------------------ #
    def get_layout_state(self, workspace_id: uuid.UUID) -> LayoutState | None:
        record = self._workspaces.get(workspace_id)
        if record is None:
            return None
        if record.layout is not None:
            return record.layout.model_copy(deep=True)
        return LayoutState(workspace_id=workspace_id)

    def snapshot_layout(
        self, workspace_id: uuid.UUID, layout: LayoutState
    ) -> LayoutState:
        if workspace_id not in self._workspaces:
            raise UnknownWorkspaceError(workspace_id)
        record = self._workspaces[workspace_id]
        if record.layout is not None and (
            record.layout.model_dump() == layout.model_dump()
        ):
            return record.layout.model_copy(deep=True)
        updated = record.model_copy(
            update={"layout": layout, "updated_at": _now()}
        )
        self._workspaces[workspace_id] = updated
        return layout.model_copy(deep=True)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    async def save_workspace(self, workspace_id: uuid.UUID) -> Workspace:
        record = self._workspaces.get(workspace_id)
        if record is None:
            raise UnknownWorkspaceError(workspace_id)
        await self._store.save(record.model_copy(deep=True))
        await self._publish(
            evt.WORKSPACE_SAVED,
            {"workspace_id": str(workspace_id)},
        )
        return record.model_copy()

    async def restore_workspace(self, workspace_id: uuid.UUID) -> Workspace | None:
        loaded = await self._store.load(workspace_id)
        if loaded is None:
            return None
        self._workspaces[workspace_id] = loaded.model_copy(deep=True)
        await self._publish(
            evt.WORKSPACE_OPENED,
            {
                "workspace_id": str(workspace_id),
                "name": loaded.name,
                "source": "persistence",
            },
        )
        return loaded.model_copy()

    async def list_persisted_ids(self) -> list[uuid.UUID]:
        return await self._store.list_ids()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                event_type=event_type,
                source_module=SOURCE_MODULE,
                correlation_id=uuid.uuid4(),
                payload=payload,
            )
        )


def _now():  # type: ignore[no-untyped-def]
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


__all__ = [
    "WorkspaceManager",
    "InMemoryWorkspaceStore",
    "JsonFileWorkspaceStore",
]
