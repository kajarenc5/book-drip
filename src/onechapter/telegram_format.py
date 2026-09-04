from __future__ import annotations

import html
import re

TELEGRAM_LIMIT = 3900


def format_chapter_messages(
    *,
    day: int,
    total: int,
    title: str,
    part: str | None,
    body: str,
    next_title: str | None,
    book_title: str,
) -> list[str]:
    header = f"Day {day} of {total}"
    if part:
        header += f" · {html.escape(part)}"
    header += f"\n<b>{html.escape(title)}</b>"
    footer_bits = [html.escape(book_title)]
    if next_title:
        footer_bits.append(f"Tomorrow: {html.escape(next_title)}")
    footer = "\n".join(footer_bits)

    escaped_body = html.escape(body)
    chunks = _split_body(escaped_body, TELEGRAM_LIMIT - len(header) - len(footer) - 20)
    messages = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            text = f"{header}\n\n{chunk}"
        else:
            text = f"{header} (continued)\n\n{chunk}"
        if i == len(chunks) - 1:
            text += f"\n\n—\n{footer}"
        messages.append(text)
    return messages


def _split_body(body: str, limit: int) -> list[str]:
    if len(body) <= limit:
        return [body]
    paragraphs = re.split(r"\n\n+", body)
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for para in paragraphs:
        extra = len(para) + (2 if current else 0)
        if current and size + extra > limit:
            chunks.append("\n\n".join(current))
            current = [para]
            size = len(para)
        else:
            current.append(para)
            size += extra
    if current:
        chunks.append("\n\n".join(current))
    # Hard-split any oversized leftover paragraph.
    flat: list[str] = []
    for chunk in chunks:
        if len(chunk) <= limit:
            flat.append(chunk)
            continue
        for i in range(0, len(chunk), limit):
            flat.append(chunk[i : i + limit])
    return flat or [body[:limit]]
