"""Markdown → WhatsApp markup conversion.

Converts agent Markdown output into WhatsApp-native markup tokens so that
replies render nicely in WhatsApp instead of leaking raw Markdown syntax.

WhatsApp supports a subset of inline formatting:
    *bold*  _italic_  ~strikethrough~  `monospace`  ```code block```
    - bullet   1. numbered   > blockquote

Everything else (headings, tables, links, etc.) is approximated using the
available primitives.  See plan for full conversion table.
"""

from __future__ import annotations

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
tasklists_plugin(_md)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEADING_RULE_CHAR = "\u2594"  # ▔ UPPER ONE EIGHTH BLOCK
HEADING_RULE_LEN = 25
HR_CHAR = "\u2500"  # ─ BOX DRAWINGS LIGHT HORIZONTAL
HR_LEN = 25

# ASCII chars for tables (unicode box-drawing misaligns on Android WhatsApp)
_TBL_H = "-"
_TBL_V = "|"
_TBL_TL = "+"
_TBL_TR = "+"
_TBL_BL = "+"
_TBL_BR = "+"
_TBL_T = "+"
_TBL_B = "+"
_TBL_ML = "+"
_TBL_MR = "+"
_TBL_MC = "+"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class _WhatsAppRenderer:
    """Walk a markdown-it token stream and produce WhatsApp text."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        # Stack tracking context: ("list", depth) etc.
        self._list_depth: int = 0
        self._ordered_stack: list[list[int]] = []  # per-depth counters
        self._in_blockquote: int = 0
        self._link_href: str = ""
        self._link_text_parts: list[str] = []
        self._in_link: bool = False
        self._table_rows: list[list[str]] = []
        self._table_header_rows: int = 0
        self._current_row: list[str] = []
        self._current_cell_parts: list[str] = []
        self._in_table: bool = False
        self._in_cell: bool = False

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _emit(self, text: str) -> None:
        if self._in_link:
            self._link_text_parts.append(text)
        elif self._in_cell:
            self._current_cell_parts.append(text)
        else:
            self._parts.append(text)

    def _ensure_blank_line(self) -> None:
        """Ensure the output so far ends with exactly two newlines (blank line)."""
        joined = "".join(self._parts).rstrip(" \t")
        # Strip trailing newlines, then add exactly two.
        joined = joined.rstrip("\n")
        self._parts = [joined + "\n\n"] if joined else []

    def _ensure_newline(self) -> None:
        """Ensure the output ends with at least one newline."""
        joined = "".join(self._parts)
        if joined and not joined.endswith("\n"):
            self._parts.append("\n")

    # ------------------------------------------------------------------
    # Token rendering
    # ------------------------------------------------------------------

    def render(self, tokens: list) -> str:  # noqa: PLR0912, PLR0915
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            t = tok.type

            # --- paragraphs ---
            # Inside tight lists markdown-it marks paragraph_open/close as
            # hidden — skip them so tight list items don't get blank lines.
            if t == "paragraph_open":
                pass
            elif t == "paragraph_close":
                if not tok.hidden:
                    self._ensure_blank_line()

            # --- headings ---
            elif t == "heading_open":
                self._ensure_blank_line()
                level = tok.tag  # "h1" / "h2" / "h3"
                # Collect the inline content (next token is always "inline")
                i += 1
                inline_tok = tokens[i]
                text = self._render_inline(inline_tok.children or [])
                if level == "h1":
                    self._parts.append(f"*{text.upper()}*\n")
                    self._parts.append(HEADING_RULE_CHAR * HEADING_RULE_LEN + "\n")
                    # No blank line after H1 rule (▔ glyph provides spacing)
                elif level == "h2":
                    self._parts.append(f"*{text.upper()}*\n\n")
                else:  # h3 and deeper
                    self._parts.append(f"*{text.title()}*\n\n")
                i += 1  # skip heading_close
            elif t == "heading_close":
                pass  # already consumed above

            # --- horizontal rule ---
            elif t == "hr":
                self._ensure_blank_line()
                self._parts.append(HR_CHAR * HR_LEN + "\n\n")

            # --- fenced code block ---
            elif t == "fence":
                self._ensure_newline()
                # Strip language label — just keep the code.
                code = tok.content
                self._parts.append("```\n" + code + "```\n")
                self._ensure_blank_line()

            # --- inline code (inside paragraph) ---
            elif t == "code_inline":
                self._emit(f"`{tok.content}`")

            # --- blockquote ---
            elif t == "blockquote_open":
                self._in_blockquote += 1
            elif t == "blockquote_close":
                self._in_blockquote -= 1
                self._ensure_blank_line()

            # --- bullet / ordered list ---
            elif t == "bullet_list_open":
                self._list_depth += 1
            elif t == "bullet_list_close":
                self._list_depth -= 1
                if self._list_depth == 0:
                    self._ensure_blank_line()
            elif t == "ordered_list_open":
                self._list_depth += 1
                # track counter per depth
                while len(self._ordered_stack) < self._list_depth:
                    self._ordered_stack.append([0])
                self._ordered_stack[self._list_depth - 1] = [0]
            elif t == "ordered_list_close":
                self._list_depth -= 1
                if self._list_depth == 0:
                    self._ensure_blank_line()

            elif t == "list_item_open":
                pass
            elif t == "list_item_close":
                pass

            # --- inline (paragraph / list item / blockquote / cell content) ---
            elif t == "inline":
                text = self._render_inline(tok.children or [])
                if self._in_cell:
                    # Cell content — route via _emit() so it lands in
                    # _current_cell_parts, not the main output.
                    self._emit(text)
                elif self._in_blockquote:
                    # Prefix each line with "> "
                    lines = text.splitlines()
                    prefixed = "\n".join("> " + ln for ln in lines)
                    self._parts.append(prefixed + "\n")
                elif self._list_depth > 0:
                    # Check if this inline is a task-list item (mdit_py_plugins
                    # inserts checkbox HTML as the first child text).
                    task_text = _extract_task(tok.children or [])
                    if task_text is not None:
                        prefix = _list_prefix(self._list_depth, task=True)
                        self._parts.append(prefix + task_text + "\n")
                    else:
                        # Check if we're in an ordered list context
                        prefix = _list_prefix(self._list_depth)
                        if self._ordered_stack and self._list_depth <= len(
                            self._ordered_stack
                        ):
                            counter = self._ordered_stack[self._list_depth - 1]
                            counter[0] += 1
                            prefix = (
                                _INDENT * (self._list_depth - 1) + f"{counter[0]}. "
                            )
                        self._parts.append(prefix + text + "\n")
                else:
                    self._parts.append(text)

            # --- table ---
            elif t == "table_open":
                self._in_table = True
                self._table_rows = []
                self._table_header_rows = 0
            elif t == "table_close":
                self._in_table = False
                self._ensure_blank_line()
                self._parts.append(self._render_table() + "\n")
                self._ensure_blank_line()
                self._table_rows = []

            elif t == "thead_open":
                self._table_header_rows = 0
            elif t == "thead_close":
                self._table_header_rows = len(self._table_rows)

            elif t in ("tbody_open", "tbody_close"):
                pass

            elif t == "tr_open":
                self._current_row = []
            elif t == "tr_close":
                self._table_rows.append(self._current_row)

            elif t in ("th_open", "td_open"):
                self._in_cell = True
                self._current_cell_parts = []
            elif t in ("th_close", "td_close"):
                self._in_cell = False
                self._current_row.append("".join(self._current_cell_parts).strip())

            i += 1

        result = "".join(self._parts).strip()
        return result

    # ------------------------------------------------------------------
    # Inline rendering
    # ------------------------------------------------------------------

    def _render_inline(self, children: list) -> str:  # noqa: PLR0912
        """Render inline token children to a WhatsApp string."""
        parts: list[str] = []
        link_href = ""
        link_text: list[str] = []
        in_link = False

        i = 0
        while i < len(children):
            tok = children[i]
            t = tok.type

            if t == "text":
                # mdit_py_plugins task-list: checkbox text is embedded here.
                # We handle task detection at a higher level; pass through.
                if in_link:
                    link_text.append(tok.content)
                else:
                    parts.append(tok.content)

            elif t == "softbreak":
                parts.append("\n")
            elif t == "hardbreak":
                parts.append("\n")

            elif t == "strong_open":
                parts.append("*")
            elif t == "strong_close":
                parts.append("*")

            elif t == "em_open":
                parts.append("_")
            elif t == "em_close":
                parts.append("_")

            elif t == "s_open":
                parts.append("~")
            elif t == "s_close":
                parts.append("~")

            elif t == "code_inline":
                text = f"`{tok.content}`"
                if in_link:
                    link_text.append(text)
                else:
                    parts.append(text)

            elif t == "image":
                # Strip images — they're sent separately via split_segments.
                pass

            elif t == "link_open":
                in_link = True
                link_text = []
                # href is in tok.attrs as list of [key, val] or dict
                attrs = tok.attrs or {}
                if isinstance(attrs, dict):
                    link_href = attrs.get("href", "")
                else:
                    # list of [key, val] pairs
                    link_href = next((v for k, v in attrs if k == "href"), "")

            elif t == "link_close":
                in_link = False
                text = "".join(link_text).strip()
                if text and text != link_href:
                    parts.append(f"{text} ({link_href})")
                else:
                    # bare URL or text == url → just the URL (WA auto-links)
                    parts.append(link_href)
                link_href = ""
                link_text = []

            elif t == "html_inline":
                # tasklists_plugin emits checkbox html as html_inline tokens —
                # we handle task detection separately; skip raw HTML here.
                pass

            i += 1

        return "".join(parts)

    # ------------------------------------------------------------------
    # Table rendering
    # ------------------------------------------------------------------

    def _render_table(self) -> str:
        """Render collected table rows as a unicode box table in a ``` fence."""
        if not self._table_rows:
            return ""

        # Compute column count
        col_count = max(len(row) for row in self._table_rows)

        # Pad rows to uniform width
        rows = [row + [""] * (col_count - len(row)) for row in self._table_rows]

        # Compute column widths
        widths = [max(len(row[c]) for row in rows) for c in range(col_count)]

        def hline(left: str, mid: str, sep: str, right: str) -> str:
            return left + sep.join(_TBL_H * (w + 2) for w in widths) + right

        def data_row(row: list[str]) -> str:
            cells = [f" {cell:<{w}} " for cell, w in zip(row, widths)]
            return _TBL_V + _TBL_V.join(cells) + _TBL_V

        lines: list[str] = []
        lines.append(hline(_TBL_TL, _TBL_T, _TBL_T, _TBL_TR))
        for i, row in enumerate(rows):
            lines.append(data_row(row))
            if i == self._table_header_rows - 1 and i < len(rows) - 1:
                # After the last header row, add divider
                lines.append(hline(_TBL_ML, _TBL_MC, _TBL_MC, _TBL_MR))
        lines.append(hline(_TBL_BL, _TBL_B, _TBL_B, _TBL_BR))

        table_str = "\n".join(lines)
        return "```\n" + table_str + "\n```"


# ---------------------------------------------------------------------------
# Task-list helpers
# ---------------------------------------------------------------------------


def _extract_task(children: list) -> str | None:
    """If the inline token children represent a task-list item, return the text.

    mdit_py_plugins injects an ``html_inline`` token containing the
    ``<input ...>`` checkbox as the very first child.  We look for that
    marker and return the item text with an emoji prefix, or ``None`` if
    this is not a task item.
    """
    if not children:
        return None
    first = children[0]
    if first.type != "html_inline":
        return None
    html = first.content
    if 'type="checkbox"' not in html:
        return None
    checked = 'checked="checked"' in html or "checked" in html
    emoji = "✅" if checked else "⬜"
    # Remaining children form the item text.
    renderer = _WhatsAppRenderer()
    text = renderer._render_inline(children[1:]).strip()
    return f"{emoji} {text}"


_INDENT = "\u2800\u2800\u2800\u2800"  # 4 × Braille blank — not stripped by WhatsApp


def _list_prefix(depth: int, task: bool = False) -> str:
    """Return the bullet prefix string for a list item at the given depth."""
    if task:
        return ""  # task items handle their own prefix
    if depth == 1:
        return "- "
    indent = _INDENT * (depth - 1)
    return f"{indent}\u2022 "  # • bullet


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def markdown_to_whatsapp(text: str) -> str:
    """Convert Markdown text to WhatsApp-native markup.

    Handles: bold, italic, strikethrough, inline code, fenced code blocks
    (language label stripped), bullet/numbered lists, nested lists (bullet
    emoji), task lists (emoji checkboxes), blockquotes, headings (H1 all-caps
    bold + decorative underline, H2 all-caps bold, H3 title-case bold),
    horizontal rules (unicode line), links (text (url) or bare url), tables
    (unicode box table in monospace fence), images (stripped).
    """
    if not text or not text.strip():
        return text

    tokens = _md.parse(text)
    renderer = _WhatsAppRenderer()
    return renderer.render(tokens)
