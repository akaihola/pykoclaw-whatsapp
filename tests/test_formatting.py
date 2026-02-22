"""Tests for markdown_to_whatsapp() formatter.

All test cases are parametrised against the conversion table defined in
.sisyphus/plans/whatsapp-markdown-formatting.md.
"""

from __future__ import annotations

import pytest

from pykoclaw_whatsapp.formatting import (
    HEADING_RULE_CHAR,
    HEADING_RULE_LEN,
    HR_CHAR,
    HR_LEN,
    markdown_to_whatsapp,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def wa(text: str) -> str:
    """Strip trailing whitespace for comparison."""
    return text.strip()


# ---------------------------------------------------------------------------
# Inline formatting (pass-through / direct mapping)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "md, expected",
    [
        # bold
        ("**bold**", "*bold*"),
        # italic (both syntaxes map to _)
        ("*italic*", "_italic_"),
        ("_italic_", "_italic_"),
        # strikethrough
        ("~~strike~~", "~strike~"),
        # inline code
        ("`code`", "`code`"),
    ],
)
def test_inline_formatting(md: str, expected: str) -> None:
    result = markdown_to_whatsapp(md)
    assert expected in result


def test_combined_bold_italic() -> None:
    """**_bold italic_** → *_bold italic_*"""
    result = markdown_to_whatsapp("**_bold italic_**")
    assert "*" in result and "_" in result
    # bold wraps italic
    assert result.strip().startswith("*") and result.strip().endswith("*")


# ---------------------------------------------------------------------------
# Code blocks
# ---------------------------------------------------------------------------


def test_fenced_block_no_language() -> None:
    """Plain ``` block passes through unchanged."""
    md = "```\nprint('hello')\n```"
    result = markdown_to_whatsapp(md)
    assert "```" in result
    assert "print('hello')" in result


def test_fenced_block_with_language_label_stripped() -> None:
    """```python label is stripped; code content is preserved."""
    md = "```python\nprint('hello')\n```"
    result = markdown_to_whatsapp(md)
    assert "python" not in result
    assert "print('hello')" in result
    assert "```" in result


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


def test_h1_all_caps_bold_with_underline() -> None:
    """# Title → *TITLE* + 27 × ▔ underline, no blank line after."""
    result = markdown_to_whatsapp("# My Title")
    assert "*MY TITLE*" in result
    rule = HEADING_RULE_CHAR * HEADING_RULE_LEN
    assert rule in result
    # The rule must follow the heading on the very next line (no blank between)
    idx_heading = result.index("*MY TITLE*")
    idx_rule = result.index(rule)
    between = result[idx_heading + len("*MY TITLE*") : idx_rule].strip()
    assert between == "", (
        f"Expected no blank line between H1 and rule; got: {between!r}"
    )


def test_h2_all_caps_bold_no_underline() -> None:
    """## Title → *TITLE* (all caps, bold), no underline, blank line after."""
    result = markdown_to_whatsapp("## Section")
    assert "*SECTION*" in result
    assert HEADING_RULE_CHAR not in result
    # ends with two newlines (blank line)
    assert result.endswith("\n\n") or result.rstrip("\n") + "\n\n" == result + "\n\n"


def test_h3_title_case_bold_no_underline() -> None:
    """### Title → *Title* (title-case bold), no underline, blank line after."""
    result = markdown_to_whatsapp("### my heading")
    assert "*My Heading*" in result
    assert HEADING_RULE_CHAR not in result


def test_multiple_headings_blank_line_separation() -> None:
    """Sequence of headings get correct blank-line separation."""
    md = "# First\n\n## Second\n\n### Third"
    result = markdown_to_whatsapp(md)
    assert "*FIRST*" in result
    assert HEADING_RULE_CHAR * HEADING_RULE_LEN in result
    assert "*SECOND*" in result
    assert "*Third*" in result


# ---------------------------------------------------------------------------
# Horizontal rule
# ---------------------------------------------------------------------------


def test_hr_emits_unicode_line() -> None:
    """--- → 25 × ─, surrounded by blank lines."""
    md = "Before\n\n---\n\nAfter"
    result = markdown_to_whatsapp(md)
    rule = HR_CHAR * HR_LEN
    assert rule in result
    idx = result.index(rule)
    before = result[:idx]
    after = result[idx + len(rule) :]
    assert before.endswith("\n\n"), f"expected blank line before hr; got: {before!r}"
    assert after.startswith("\n\n"), f"expected blank line after hr; got: {after!r}"


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_bullet_list() -> None:
    """Tight bullet list — no blank lines between items."""
    result = markdown_to_whatsapp("- apple\n- banana\n- cherry")
    assert result == "- apple\n- banana\n- cherry"


def test_numbered_list() -> None:
    """Tight numbered list — no blank lines between items."""
    result = markdown_to_whatsapp("1. first\n2. second\n3. third")
    assert result == "1. first\n2. second\n3. third"


def test_nested_list_depth_2() -> None:
    """Nested list at depth 2 uses 4 × Braille-blank indent + • bullet."""
    md = "- top\n  - sub"
    result = markdown_to_whatsapp(md)
    assert "- top" in result
    assert "\u2800\u2800\u2800\u2800\u2022 sub" in result


def test_nested_list_depth_3() -> None:
    """Nested list at depth 3 uses 8 × Braille-blank indent + • bullet."""
    md = "- top\n  - mid\n    - deep"
    result = markdown_to_whatsapp(md)
    assert "\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2800\u2022 deep" in result


# ---------------------------------------------------------------------------
# Task lists
# ---------------------------------------------------------------------------


def test_unchecked_task() -> None:
    """- [ ] task → ⬜ task"""
    result = markdown_to_whatsapp("- [ ] buy milk")
    assert "\u2b1c buy milk" in result or "\u2b1c" in result


def test_checked_task() -> None:
    """- [x] task → ✅ task"""
    result = markdown_to_whatsapp("- [x] done thing")
    assert "\u2705 done thing" in result or "\u2705" in result


# ---------------------------------------------------------------------------
# Blockquote
# ---------------------------------------------------------------------------


def test_blockquote() -> None:
    """> quote → > quote (pass-through prefix)."""
    result = markdown_to_whatsapp("> This is a quote")
    assert "> This is a quote" in result


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def test_link_text_differs_from_url() -> None:
    """[text](url) → text (url)"""
    result = markdown_to_whatsapp("[Click here](https://example.com)")
    assert "Click here (https://example.com)" in result


def test_bare_link_url_only() -> None:
    """[https://example.com](https://example.com) → https://example.com"""
    result = markdown_to_whatsapp("[https://example.com](https://example.com)")
    assert "https://example.com" in result
    # Should not repeat URL inside parens when text == url
    assert "(https://example.com)" not in result


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def test_table_renders_as_ascii_box_in_fence() -> None:
    """Markdown table → ASCII box table wrapped in ``` fence."""
    md = "| Name  | Score |\n|-------|-------|\n| Alice | 42    |\n| Bob   | 17    |"
    result = markdown_to_whatsapp(md)
    assert "```" in result
    assert "|" in result
    assert "+" in result
    assert "Alice" in result
    assert "Bob" in result
    assert "42" in result


def test_table_text_before_borders() -> None:
    """Cell text must appear inside the table, not before the border lines."""
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = markdown_to_whatsapp(md)
    # The ``` fence must open before any cell content appears
    fence_pos = result.index("```")
    for cell in ("A", "B", "1", "2"):
        cell_pos = result.index(cell)
        assert cell_pos > fence_pos, (
            f"Cell content {cell!r} appears before opening ``` fence"
        )


def test_table_header_divider() -> None:
    """Header row is separated from body by +---+---+ divider."""
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = markdown_to_whatsapp(md)
    lines = result.strip().splitlines()
    # Find header row index (after opening +---+ line)
    divider_lines = [
        i for i, ln in enumerate(lines) if ln.startswith("+") and "-" in ln
    ]
    # There should be 3 dividers: top, header-body separator, bottom
    assert len(divider_lines) == 3, f"Expected 3 divider lines, got: {divider_lines}"
    # Header row sits between first and second dividers
    header_row = lines[divider_lines[0] + 1]
    assert "A" in header_row and "B" in header_row


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def test_image_stripped() -> None:
    """![alt](path) → stripped (image sent separately via split_segments)."""
    result = markdown_to_whatsapp("![screenshot](./img.png)")
    assert "screenshot" not in result
    assert "img.png" not in result
    assert "!" not in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty() -> None:
    assert markdown_to_whatsapp("") == ""


def test_whitespace_only_input() -> None:
    assert markdown_to_whatsapp("   \n  ") == "   \n  "


def test_plain_text_unchanged() -> None:
    """Plain text with no Markdown syntax passes through unchanged."""
    plain = "Hello, world! This is plain text."
    result = markdown_to_whatsapp(plain)
    assert plain in result


def test_plain_text_no_extra_markup() -> None:
    """Plain text should not gain any WhatsApp markup tokens."""
    plain = "Just some words here."
    result = markdown_to_whatsapp(plain)
    for char in ("*", "_", "~", "`"):
        assert char not in result, f"Unexpected markup char {char!r} in: {result!r}"
