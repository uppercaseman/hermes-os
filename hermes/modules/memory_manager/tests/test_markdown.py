import uuid

from hermes.modules.memory_manager.markdown import entry_to_markdown
from hermes.modules.memory_manager.models import MemoryEntry


def test_renders_frontmatter_with_tags():
    entry = MemoryEntry(scope="persistent", key="project-notes", value={"summary": "hello"}, tags=["important", "draft"])

    markdown = entry_to_markdown(entry)

    assert "scope: persistent" in markdown
    assert "  - important" in markdown
    assert "  - draft" in markdown
    assert "# project-notes" in markdown
    assert "**summary**: hello" in markdown


def test_omits_tags_section_when_no_tags():
    entry = MemoryEntry(scope="persistent", key="k", value={})

    markdown = entry_to_markdown(entry)

    assert "tags:" not in markdown


def test_renders_backlinks_as_wiki_links_with_titles():
    linked_id = uuid.uuid4()
    entry = MemoryEntry(scope="persistent", key="k", value={}, backlinks=[linked_id])

    markdown = entry_to_markdown(entry, backlink_titles={linked_id: "Other Note"})

    assert "## Backlinks" in markdown
    assert "[[Other Note]]" in markdown


def test_backlink_without_a_known_title_falls_back_to_raw_id():
    linked_id = uuid.uuid4()
    entry = MemoryEntry(scope="persistent", key="k", value={}, backlinks=[linked_id])

    markdown = entry_to_markdown(entry)

    assert f"[[{linked_id}]]" in markdown


def test_omits_backlinks_section_when_no_backlinks():
    entry = MemoryEntry(scope="persistent", key="k", value={})

    markdown = entry_to_markdown(entry)

    assert "## Backlinks" not in markdown


def test_includes_owner_agent_id_when_present():
    entry = MemoryEntry(scope="persistent", key="k", value={}, owner_agent_id="agent-a")

    markdown = entry_to_markdown(entry)

    assert "owner_agent_id: agent-a" in markdown
