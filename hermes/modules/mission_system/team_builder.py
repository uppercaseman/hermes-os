"""TeamBuilder -- creates and dissolves TEMPORARY specialist roles for
one mission.

Not specialist agents: no AI/model logic, no prompts, no execution.
This determines and grants scoped PERMISSIONS -- which capabilities,
tools, and memory a role may touch -- by matching a mission's
requirements against a registry of role templates.

Memory access is genuinely enforced, not just declared: each role gets a
unique `agent_id` and a real grant (via Memory Manager's already-built
`grant_permission`) to read/write the mission's SHARED memory pool
(`owner_agent_id = str(mission.id)`), on top of the automatic, ownership-
based access Memory Manager already gives every agent to its own
private memory. Tool access (`allowed_tools`) is declarative only --
Tool Manager has no agent-scoped enforcement point to hook into yet, so
`SpecialistRole.can_use_tool()` is a data check a future real agent
implementation would consult, not something this framework enforces
itself.
"""
from __future__ import annotations

from hermes.modules.mission_system.contracts import MemoryPermissionGranter
from hermes.modules.mission_system.errors import UnknownRoleTemplateError
from hermes.modules.mission_system.models import Mission, SpecialistRole
from hermes.modules.mission_system.roles import DEFAULT_ROLE_TEMPLATES, RoleTemplate


class TeamBuilder:
    def __init__(
        self,
        *,
        memory_manager: MemoryPermissionGranter | None = None,
        templates: list[RoleTemplate] | None = None,
    ) -> None:
        self._memory_manager = memory_manager
        self._templates: dict[str, RoleTemplate] = {t.role_name: t for t in (templates or DEFAULT_ROLE_TEMPLATES)}

    def register_template(self, template: RoleTemplate) -> None:
        """Registers (or replaces) a role template. The six example
        roles are defaults, not the only roles available."""
        self._templates[template.role_name] = template

    def determine_required_roles(self, mission: Mission) -> list[str]:
        """Explicit `Mission.requested_roles` always wins; otherwise
        infers roles whose `trigger_capabilities` intersect the
        mission's `required_capabilities`."""
        if mission.requested_roles:
            return list(mission.requested_roles)
        required = set(mission.required_capabilities)
        return [template.role_name for template in self._templates.values() if required & set(template.trigger_capabilities)]

    def build_team(self, mission: Mission) -> list[SpecialistRole]:
        """Builds the temporary team: one `SpecialistRole` per required
        role name, each with its own `agent_id` scoped to this mission
        and granted access to the mission's shared memory pool. Raises
        `UnknownRoleTemplateError` for an explicitly requested role with
        no matching template."""
        role_names = self.determine_required_roles(mission)
        mission_pool_owner = str(mission.id)
        roles: list[SpecialistRole] = []
        for role_name in role_names:
            template = self._templates.get(role_name)
            if template is None:
                raise UnknownRoleTemplateError(role_name)
            agent_id = f"mission:{mission.id}:{role_name}"
            role = SpecialistRole(
                role_name=role_name,
                mission_id=mission.id,
                agent_id=agent_id,
                required_capabilities=list(template.default_capabilities),
                allowed_tools=list(template.default_tools),
                memory_scopes=list(template.default_memory_scopes),
            )
            roles.append(role)
            if self._memory_manager is not None:
                self._memory_manager.grant_permission(
                    agent_id, owner_agent_id=mission_pool_owner, can_read=True, can_write=True
                )
        return roles

    def dissolve_team(self, mission: Mission) -> None:
        """Ends the mission's team: revokes each role's access to the
        shared mission memory pool and marks every role dissolved.
        Private, role-owned memory entries are left intact -- Memory
        Manager has no bulk-delete-by-owner operation, and each role's
        `agent_id` is unique to this one mission and never reused, so
        leaving them is inert, not a leak."""
        mission_pool_owner = str(mission.id)
        for role in mission.assigned_team:
            role.status = "dissolved"
            if self._memory_manager is not None:
                self._memory_manager.revoke_permission(role.agent_id, owner_agent_id=mission_pool_owner)
