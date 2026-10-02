"""Small, exact edits to Obsidian Markdown notes.

A *section* is the body under a heading: every line after it up to the next heading of the same or
higher level (fewer or equal `#`), or the end of the file. Headings inside fenced code blocks are
ignored. Everything outside the edited span is kept byte for byte.
"""
from __future__ import annotations

import re

import yaml

HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE = re.compile(r"^[ \t]*(```|~~~)")


class MarkdownError(ValueError):
    pass


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line index, level, text) for every heading outside code fences."""
    out, fence = [], None
    for i, line in enumerate(lines):
        m = FENCE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        if fence:
            continue
        h = HEADING.match(line.rstrip("\n"))
        if h:
            out.append((i, len(h.group(1)), h.group(2).strip()))
    return out


def find_section(text: str, name: str) -> tuple[int, int]:
    """Return (first body line, end line) of the section titled `name` (line indexes)."""
    lines = text.splitlines(keepends=True)
    heads = _headings(lines)
    matches = [(i, lvl) for i, lvl, t in heads if t == name.strip()]
    if not matches:
        raise MarkdownError(f"no section titled {name!r}")
    if len(matches) > 1:
        raise MarkdownError(f"{len(matches)} sections are titled {name!r}; ambiguous")
    start, level = matches[0]
    end = next((i for i, lvl, _ in heads if i > start and lvl <= level), len(lines))
    return start + 1, end


def section_body(text: str, name: str) -> str:
    lines = text.splitlines(keepends=True)
    a, b = find_section(text, name)
    return "".join(lines[a:b]).strip("\n")


def _with_spacing(body: str, has_next: bool) -> list[str]:
    body = body.strip("\n")
    out = ["\n"] + [ln + "\n" for ln in body.split("\n")] if body else ["\n"]
    if has_next:
        out.append("\n")
    return out


def replace_section(text: str, name: str, new_body: str) -> str:
    lines = text.splitlines(keepends=True)
    a, b = find_section(text, name)
    if lines and not lines[a - 1].endswith("\n"):
        lines[a - 1] += "\n"
    return "".join(lines[:a] + _with_spacing(new_body, b < len(lines)) + lines[b:])


def append_to_list(text: str, name: str, item: str) -> str:
    item = item.strip("\n")
    if "\n" in item or not re.match(r"^\s*([-*+]|\d+\.)\s+\S", item):
        raise MarkdownError("add-to-list takes exactly one list item, like '- text'")
    lines = text.splitlines(keepends=True)
    a, b = find_section(text, name)
    body = lines[a:b]
    last_item = max((i for i, ln in enumerate(body) if re.match(r"^\s*([-*+]|\d+\.)\s+", ln)),
                    default=None)
    if last_item is None:
        current = "".join(body).strip("\n")
        new_body = f"{current}\n\n{item}" if current else item
        return replace_section(text, name, new_body)
    insert_at = a + last_item + 1
    if not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    return "".join(lines[:insert_at] + [item + "\n"] + lines[insert_at:])


def _frontmatter_span(lines: list[str]) -> tuple[int, int]:
    if not lines or lines[0].rstrip("\n") != "---":
        raise MarkdownError("note has no frontmatter")
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n") == "---":
            return 1, i
    raise MarkdownError("frontmatter is not closed")


def frontmatter_value(text: str, key: str) -> str | None:
    lines = text.splitlines(keepends=True)
    a, b = _frontmatter_span(lines)
    pat = re.compile(rf"^{re.escape(key)}:(.*)$")
    for ln in lines[a:b]:
        m = pat.match(ln.rstrip("\n"))
        if m:
            return m.group(1).strip()
    return None


def set_frontmatter(text: str, key: str, value: str) -> str:
    """Set a top-level scalar or flow-list field. Block lists under the key are replaced too."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise MarkdownError(f"bad frontmatter key {key!r}")
    value = value.strip()
    if "\n" in value:
        raise MarkdownError("frontmatter values must be one line (scalar or [flow, list])")
    try:
        yaml.safe_load(f"{key}: {value}")
    except yaml.YAMLError as exc:
        raise MarkdownError(f"not valid YAML: {exc}") from None
    lines = text.splitlines(keepends=True)
    a, b = _frontmatter_span(lines)
    pat = re.compile(rf"^{re.escape(key)}:")
    for i in range(a, b):
        if pat.match(lines[i]):
            j = i + 1           # drop an indented block (e.g. "  - item") that belonged to the key
            while j < b and re.match(r"^\s+\S|^\s*-\s", lines[j]):
                j += 1
            return "".join(lines[:i] + [f"{key}: {value}\n"] + lines[j:])
    return "".join(lines[:b] + [f"{key}: {value}\n"] + lines[b:])


def frontmatter_ok(text: str) -> bool:
    try:
        lines = text.splitlines(keepends=True)
        a, b = _frontmatter_span(lines)
        return isinstance(yaml.safe_load("".join(lines[a:b])) or {}, dict)
    except (MarkdownError, yaml.YAMLError):
        return False
