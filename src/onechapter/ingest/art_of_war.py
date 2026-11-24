"""Profile metadata and heading rules for Sun Tzu's Искусство войны (Russian The Art of War).

Sun Tzu's treatise consists of 13 canonical chapters (Глава 1–13 / Глава I–XIII).
Russian editions (notably N. I. Konrad's translation) commonly include a translator's
preface at the front, footnote/endnote markers (`[1]`, `[*]`, `[**]`, `[***]`) in
headings and prose, and lengthy chapter-by-chapter commentaries at the back starting
with lines like `Глава 1[*]. Некоторые особенно спорные места перевода...` and `1.1. ...`.
This profile isolates cleanly the 13 chapters with canonical Russian titles and prose.
"""

from __future__ import annotations

import re

from onechapter.ingest.textutil import fold, squeeze

CANONICAL_SLUG = "iskusstvo-voyny"
CANONICAL_TITLE = "Искусство войны"
CANONICAL_AUTHOR = "Сунь-цзы"
CANONICAL_PART = "Искусство войны"

AUTHOR_HINTS = (
    "сунь-цзы",
    "сунь цзы",
    "суньцзы",
    "sun-tzu",
    "sun tzu",
    "suntzu",
    "sun-tczi",
    "sun_tczi",
    "sun tczi",
)
TITLE_HINTS = (
    "искусство войны",
    "iskusstvo_vojni",
    "iskusstvo-vojni",
    "iskusstvo voyny",
    "iskusstvo-voyny",
    "трактат о военном искусстве",
)

CHAPTER_NUMBERS: tuple[int, ...] = tuple(range(1, 14))
ROMAN_NUMERALS: tuple[str, ...] = (
    "I",
    "II",
    "III",
    "IV",
    "V",
    "VI",
    "VII",
    "VIII",
    "IX",
    "X",
    "XI",
    "XII",
    "XIII",
)
_ROMAN_TO_NUM = {roman: idx for idx, roman in enumerate(ROMAN_NUMERALS, start=1)}
_VALID_NUMS = set(CHAPTER_NUMBERS)

# Matches headings like "Глава 1. Предварительные расчеты",
# "Глава 1[*]. Предварительные расчеты [**]", or "ГЛАВА XIII".
CHAPTER_HEADING_RE = re.compile(
    r"^\s*глава\s+([ivx]+|\d{1,2})(?:\s*\[(?:\*+|\d+)\])?(?:[.:—\-]\s*(.*))?\s*$",
    re.IGNORECASE,
)

# Back-matter signals specific to Russian Art of War editions:
# either standard endnote headings, a chapter-1 footnote line after chapter 13,
# or numbered commentary paragraphs ("1.1. В комментаторской литературе...").
COMMENTARY_START_RE = re.compile(
    r"^\s*(?:глава\s+(?:1|i)\s*\[(?:\*+|\d+)\]|1\.1\.\s+\S)",
    re.IGNORECASE,
)

SKIP_AFTER_TITLES: tuple[str, ...] = (
    "Примечания",
    "Комментарии",
    "Послесловие",
    "Приложения",
    "Приложение",
    "Указатель имен",
    "Именной указатель",
    "Библиография",
    "Словарь",
    "Содержание",
    "Оглавление",
    "Об авторе",
)

_ORNAMENT_LINE_RE = re.compile(r"^[\s*\-—–•·~_]+$")
_NOTE_MARKER_RE = re.compile(r"\s*\[(?:\d+|\*+)\]")


def looks_like_art_of_war(
    filename: str,
    text: str,
    *,
    title: str | None = None,
    author: str | None = None,
) -> bool:
    header_blob = fold(f"{filename}\n{title or ''}\n{author or ''}\n{text[:10000]}").lower()
    if not any(hint in header_blob for hint in AUTHOR_HINTS):
        return False
    if not any(hint in header_blob for hint in TITLE_HINTS):
        return False
    chapter_hits = len(
        re.findall(
            r"^\s*глава\s+(?:[ivx]+|\d{1,2})(?:\s*\[(?:\*+|\d+)\])?(?:[.:—\-]|\s*$)",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )
    return chapter_hits >= 10


def parse_chapter_number(raw: str) -> int | None:
    token = raw.strip().upper()
    if token in _ROMAN_TO_NUM:
        return _ROMAN_TO_NUM[token]
    if token.isdigit():
        num = int(token)
        if num in _VALID_NUMS:
            return num
    return None


def is_plausible_inline_subtitle(raw: str) -> bool:
    """Reject full-sentence commentary paragraphs attached to 'Глава N[*]. ...'."""
    cleaned = clean_subtitle(raw)
    if not cleaned:
        return True
    if len(cleaned) > 100 or len(cleaned.split()) > 12:
        return False
    # Multiple sentences (period followed by space and another word) indicate commentary prose.
    if re.search(r"[.!?]\s+\S", cleaned):
        return False
    return True


def clean_subtitle(raw: str) -> str:
    cleaned = _NOTE_MARKER_RE.sub("", raw or "")
    cleaned = squeeze(cleaned).strip(" .")
    if not cleaned:
        return ""
    letters = [ch for ch in cleaned if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters):
        cleaned = cleaned.lower()
    return cleaned[0].upper() + cleaned[1:]


def clean_prose_body(body: str, *, strip_note_refs: bool = True) -> str:
    lines = body.split("\n")
    while lines and (not lines[0].strip() or _ORNAMENT_LINE_RE.match(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _ORNAMENT_LINE_RE.match(lines[-1])):
        lines.pop()
    cleaned_lines: list[str] = []
    for line in lines:
        if _ORNAMENT_LINE_RE.match(line) and line.strip():
            continue
        if strip_note_refs:
            line = _NOTE_MARKER_RE.sub("", line)
            line = re.sub(r"[ \t]{2,}", " ", line)
            line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        cleaned_lines.append(line.rstrip())
    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
