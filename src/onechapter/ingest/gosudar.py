"""Profile metadata and heading rules for Machiavelli's Государь (Russian The Prince).

Machiavelli's treatise consists of 27 daily units: the epistolary Dedication to
Lorenzo de' Medici followed by Chapters I–XXVI. Russian editions frequently
wrap the treatise with publisher annotations, translator prefaces, tables of
contents, and endnotes ('Примечания' / 'Комментарии'); this profile isolates
only the Dedication and Chapters I–XXVI with clean Cyrillic titles and prose.
"""

from __future__ import annotations

import re

from onechapter.ingest.textutil import fold, squeeze

CANONICAL_SLUG = "gosudar"
CANONICAL_TITLE = "Государь"
CANONICAL_AUTHOR = "Никколо Макиавелли"
CANONICAL_PART = "Государь"
CANONICAL_DEDICATION_TITLE = "Посвящение Лоренцо деи Медичи"
CANONICAL_SALUTATION = "Никколо Макиавелли — его светлости Лоренцо деи Медичи"

AUTHOR_HINTS = (
    "макиавелли",
    "макьявелли",
    "makiavelli",
    "machiavelli",
)
TITLE_HINTS = (
    "государь",
    "gosudar",
)

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
    "XIV",
    "XV",
    "XVI",
    "XVII",
    "XVIII",
    "XIX",
    "XX",
    "XXI",
    "XXII",
    "XXIII",
    "XXIV",
    "XXV",
    "XXVI",
)
_ARABIC_TO_ROMAN = {str(idx): roman for idx, roman in enumerate(ROMAN_NUMERALS, start=1)}
_VALID_ROMANS = set(ROMAN_NUMERALS)

CHAPTER_HEADING_RE = re.compile(
    r"^\s*глава\s+([ivxlcdm]+|\d{1,2})(?:[.:—\-]\s*(.*))?\s*$",
    re.IGNORECASE,
)

DEDICATION_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:ник+оло\s+мак(?:иа|ья|ия)велли\s*[-—–:]\s*)?(?:его\s+светлости|великолепному)\s+лоренцо.*медичи"
    r"|посвящение(?:\s*[.:—\-].*)?"
    r")\s*$",
    re.IGNORECASE,
)

FRONT_EDITORIAL_HEADING_RE = re.compile(
    r"^\s*(?:предисловие|вступительная\s+статья|от\s+переводчика|от\s+издательства|об\s+авторе|биография|оглавление|содержание)\b.*$",
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
    "Содержание",
    "Оглавление",
    "Об авторе",
)

# Proper-noun stems restored when an edition prints chapter subtitles in ALL CAPS.
_PROPER_WORD_PREFIXES: tuple[tuple[str, str], ...] = (
    ("александр", "Александр"),
    ("дари", "Дари"),
    ("итали", "Итали"),
    ("лоренцо", "Лоренцо"),
    ("медичи", "Медичи"),
    ("рим", "Рим"),
    ("франци", "Франци"),
    ("цезар", "Цезар"),
)

_ORNAMENT_LINE_RE = re.compile(r"^[\s*\-—–•·~_]+$")
_ENDNOTE_REF_RE = re.compile(r"\*\s*\[\d+\]|\[\d+\]")


def looks_like_gosudar(
    filename: str,
    text: str,
    *,
    title: str | None = None,
    author: str | None = None,
) -> bool:
    header_blob = fold(f"{filename}\n{title or ''}\n{author or ''}\n{text[:6000]}").lower()
    if not any(hint in header_blob for hint in AUTHOR_HINTS):
        return False
    if not any(hint in header_blob for hint in TITLE_HINTS):
        return False
    chapter_hits = len(
        re.findall(r"^\s*глава\s+(?:[ivxlcdm]+|\d{1,2})\b", text, flags=re.IGNORECASE | re.MULTILINE)
    )
    return chapter_hits >= 10


def parse_chapter_numeral(raw: str) -> str | None:
    token = raw.strip().upper()
    if token in _VALID_ROMANS:
        return token
    return _ARABIC_TO_ROMAN.get(token)


def format_russian_subtitle(raw: str) -> str:
    cleaned = squeeze(raw).strip(" .")
    if not cleaned:
        return ""
    letters = [ch for ch in cleaned if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters):
        lowered = cleaned.lower()
        words = re.split(r"(\s+)", lowered)
        restored: list[str] = []
        first_word_done = False
        for token in words:
            if not token or token.isspace():
                restored.append(token)
                continue
            prefix_punct = ""
            core = token
            while core and not core[0].isalpha():
                prefix_punct += core[0]
                core = core[1:]
            if core:
                if core.isascii() and re.fullmatch(r"[ivxlcdm]+", core):
                    core = core.upper()
                    first_word_done = True
                else:
                    for stem_low, stem_cap in _PROPER_WORD_PREFIXES:
                        if core.startswith(stem_low):
                            core = stem_cap + core[len(stem_low) :]
                            break
                    if not first_word_done:
                        core = core[0].upper() + core[1:]
                        first_word_done = True
            restored.append(prefix_punct + core)
        return "".join(restored)
    return cleaned[0].upper() + cleaned[1:]


def clean_prose_body(body: str, *, strip_endnote_refs: bool = True) -> str:
    lines = body.split("\n")
    while lines and (not lines[0].strip() or _ORNAMENT_LINE_RE.match(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _ORNAMENT_LINE_RE.match(lines[-1])):
        lines.pop()
    cleaned_lines: list[str] = []
    for line in lines:
        if _ORNAMENT_LINE_RE.match(line) and line.strip():
            continue
        if strip_endnote_refs:
            line = re.sub(r'([.!?]["»]?)\s*\*?\s*\[\d+\]\s*\.', r"\1", line)
            line = _ENDNOTE_REF_RE.sub("", line)
            line = re.sub(r"[ \t]{2,}", " ", line)
            line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        cleaned_lines.append(line.rstrip())
    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
