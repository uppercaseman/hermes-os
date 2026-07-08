"""Pure Obsidian-flavored markdown rendering for a MemoryEntry.

No filesystem or network access -- this is the real, tested half of
"Obsidian vault integration." Actually reading/writing a vault directory
is `ObsidianVaultAdapter`'s job (adapters/obsidian.py), which is an
honest placeholder, consistent with "do not connect to live external
APIs yet" -- that instruction covers local vault I/O too, the same way
every other adapter in this codebase is a placeholder. A real
`ObsidianVaultAdapter.write_entry` would call `entry_to_markdown` and
write the result to a file; that wiring is what's left unimplemented,
not the rendering itself.
"""
from __future__ import annotations

import uuid

from hermes.modules.memory_manager.models import MemoryEntry


def entry_to_markdown(entry: MemoryEntry, *, backlink_titles: dict[uuid.UUID, str] | None = None) -> str:
    """Renders `entry` as an Obsidian note: YAML frontmatter (id, scope,
    created_at, tags) followed by the structured value and, if any
    backlinks exist, a "## Backlinks" section using `[[wiki-link]]`
    syntax. `backlink_titles` maps a linked entry's id to a human-
    readable title (its `key`); ids with no known title fall back to
    the raw id.
    """
    backlink_titles = backlink_titles or {}

    frontmatter = ["---", f"id: {entry.id}", f"scope: {entry.scope}", f"created_at: {entry.created_at.isoformat()}"]
    if entry.owner_agent_id is not None:
        frontmatter.append(f"owner_agent_id: {entry.owner_agent_id}")
    if entry.tags:
        frontmatter.append("tags:")
        frontmatter.extend(f"  - {tag}" for tag in entry.tags)
    frontmatter.append("---")

    body = [f"# {entry.key}", ""]
    for field_name, field_value in entry.value.items():
        body.append(f"**{field_name}**: {field_value}")

    if entry.backlinks:
        body.append("")
        body.append("## Backlinks")
        for linked_id in entry.backlinks:
            title = backlink_titles.get(linked_id, str(linked_id))
            body.append(f"- [[{title}]]")

    return "\n".join(frontmatter) + "\n\n" + "\n".join(body) + "\n"
