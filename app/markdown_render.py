"""Safe Markdown → HTML for album descriptions."""

from __future__ import annotations

import bleach
import markdown as md

ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "ul",
    "ol",
    "li",
    "a",
    "blockquote",
    "code",
    "pre",
    "h1",
    "h2",
    "h3",
    "h4",
    "hr",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def render_markdown(text: str | None) -> str:
    if not text or not str(text).strip():
        return ""
    raw = md.markdown(
        str(text),
        extensions=[
            "markdown.extensions.fenced_code",
            "markdown.extensions.nl2br",
            "markdown.extensions.sane_lists",
        ],
        output_format="html5",
    )
    clean = bleach.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    # Open external links in a new tab safely
    clean = bleach.linkify(
        clean,
        callbacks=[bleach.callbacks.nofollow, bleach.callbacks.target_blank],
        skip_tags=["pre", "code"],
    )
    return clean
