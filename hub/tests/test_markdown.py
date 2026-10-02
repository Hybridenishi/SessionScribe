"""Exact Markdown edits: everything outside the edited span must survive byte for byte."""
from __future__ import annotations

import pytest

from scribe_hub.proposals import markdown as md

NOTE = """---
title: Leon Blackstone
type: npc
status: alive
tags: [npc]
aliases:
  - Leon
---

# Leon Blackstone

Intro line.

## Relationships

- Trusts Angelica.
- Wary of Talos.

### Old notes

Deep detail.

## Appearance

Tall.

```markdown
## Relationships
not a heading
```
"""


def test_section_body_stops_at_same_or_higher_heading():
    assert md.section_body(NOTE, "Relationships") == (
        "- Trusts Angelica.\n- Wary of Talos.\n\n### Old notes\n\nDeep detail.")
    assert md.section_body(NOTE, "Old notes") == "Deep detail."


def test_headings_inside_code_fences_are_ignored():
    md.find_section(NOTE, "Relationships")          # would be ambiguous if the fence counted


def test_missing_and_ambiguous_sections():
    with pytest.raises(md.MarkdownError, match="no section"):
        md.find_section(NOTE, "Quotes")
    with pytest.raises(md.MarkdownError, match="ambiguous"):
        md.find_section("## A\n\nx\n\n## A\n\ny\n", "A")


def test_replace_section_keeps_everything_else_exactly():
    out = md.replace_section(NOTE, "Old notes", "Deeper detail.")
    assert out.replace("Deeper detail.", "Deep detail.") == NOTE
    assert md.section_body(out, "Old notes") == "Deeper detail."


def test_a_trailing_code_block_belongs_to_the_section_above_it():
    """So `after` must carry it: the contract says after is the whole section."""
    assert md.section_body(NOTE, "Appearance").endswith("not a heading\n```")


def test_replace_last_section_in_file():
    text = "# T\n\n## End\n\nold"
    assert md.replace_section(text, "End", "new") == "# T\n\n## End\n\nnew\n"


def test_append_to_list_goes_after_the_last_item():
    out = md.append_to_list(NOTE, "Relationships", "- Owes Fang a favor.")
    assert "- Wary of Talos.\n- Owes Fang a favor.\n\n### Old notes" in out
    assert out.replace("- Owes Fang a favor.\n", "") == NOTE


def test_append_to_list_without_a_list_starts_one():
    out = md.append_to_list("## Quotes\n\nNone yet.\n", "Quotes", "- “Hello.”")
    assert md.section_body(out, "Quotes") == "None yet.\n\n- “Hello.”"


def test_append_to_list_takes_one_item_only():
    with pytest.raises(md.MarkdownError):
        md.append_to_list(NOTE, "Relationships", "- a\n- b")
    with pytest.raises(md.MarkdownError):
        md.append_to_list(NOTE, "Relationships", "not a list item")


def test_set_frontmatter_replaces_scalars_and_block_lists():
    out = md.set_frontmatter(NOTE, "status", "dead")
    assert md.frontmatter_value(out, "status") == "dead"
    assert out.replace("status: dead", "status: alive") == NOTE
    out = md.set_frontmatter(NOTE, "aliases", "[Leon, The Captain]")
    assert "aliases: [Leon, The Captain]\n---" in out and "  - Leon" not in out


def test_set_frontmatter_adds_a_missing_key_and_rejects_bad_values():
    out = md.set_frontmatter(NOTE, "last-seen", "60")
    assert md.frontmatter_value(out, "last-seen") == "60"
    with pytest.raises(md.MarkdownError):
        md.set_frontmatter(NOTE, "status", "[unclosed")
    with pytest.raises(md.MarkdownError):
        md.set_frontmatter(NOTE, "bad key", "x")
    with pytest.raises(md.MarkdownError):
        md.set_frontmatter("# no frontmatter\n", "status", "x")


def test_frontmatter_ok():
    assert md.frontmatter_ok(NOTE)
    assert not md.frontmatter_ok("# none\n")
    assert not md.frontmatter_ok("---\n: [\n---\n")
