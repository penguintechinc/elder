"""Shared HTML sanitization for user-authored rich-text bodies.

Documents (rendered markdown) and Pages (TipTap ``body_html``) both persist
HTML authored by users. Every create/update MUST pass the HTML through
:func:`sanitize_html` before storage. Uses ``bleach`` (mandatory, >=6) with a
strict allow-list; disallowed tags/attributes/URL schemes are stripped, not
escaped, so ``<script>``, ``on*`` handlers, and ``javascript:``/``data:`` URLs
never reach the database or the DOM.
"""

from __future__ import annotations

import bleach

# Structural + inline tags permitted in rich-text bodies.
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "br",
        "hr",
        "div",
        "span",
        "section",
        "article",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "del",
        "ins",
        "sub",
        "sup",
        "mark",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "code",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "caption",
        "colgroup",
        "col",
        "a",
        "img",
    }
)

ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "*": ["id", "class"],
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "width", "height", "title"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
    "col": ["span"],
    "colgroup": ["span"],
}

# Only safe URL schemes — excludes javascript:, data:, vbscript:, file:.
ALLOWED_PROTOCOLS: list[str] = ["http", "https", "mailto"]


def sanitize_html(html_content: str) -> str:
    """Sanitize untrusted HTML against the Documents/Pages allow-list.

    Args:
        html_content: Untrusted HTML (rendered markdown or WYSIWYG output).

    Returns:
        Sanitized HTML with disallowed tags/attributes/URL schemes stripped.
    """
    if not html_content:
        return ""
    return bleach.clean(
        html_content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
