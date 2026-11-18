from __future__ import annotations

import re
from dataclasses import dataclass, field

from onechapter.ingest import tarasov
from onechapter.ingest.extract import (
    MAX_METADATA_CHARS,
    ExtractedBook,
    UploadValidationError,
    safe_html_soup,
)
from onechapter.ingest.tarasov import PARTS, SKIP_AFTER_TITLES
from onechapter.ingest.textutil import (
    normalize_title,
    paragraphize,
    squeeze,
    title_regex,
    word_count,
)

MIN_CHAPTER_WORDS = 20
MAX_SPLIT_CHAPTERS = 2_000


@dataclass
class ChapterDraft:
    index: int
    title: str
    body: str
    part: str | None = None
    source: str = "generic"
    notes: list[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return word_count(self.body)


@dataclass
class SplitResult:
    title: str
    author: str | None
    profile: str
    chapters: list[ChapterDraft]
    skipped: list[str] = field(default_factory=list)


PART_HEADING = re.compile(
    r"^\s*(глава\s+(первая|вторая|третья|четвертая|четвёртая|пятая|[0-9ivxlcdm]+)|chapter\s+[0-9ivxlcdm]+)\b",
    re.IGNORECASE,
)
GENERIC_HEADING = re.compile(
    r"^(#{1,3}\s+.+|глава\s+.+|chapter\s+[ivxlcdm0-9]+[.:].+)$",
    re.IGNORECASE,
)


def _clamp(value: str | None, fallback: str = "") -> str:
    cleaned = squeeze(value or "") or fallback
    return cleaned[:MAX_METADATA_CHARS]


def _clamp_opt(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = squeeze(value)
    return cleaned[:MAX_METADATA_CHARS] if cleaned else None


def split_extracted(book: ExtractedBook) -> SplitResult:
    if tarasov.looks_like_tarasov(book.filename, book.text):
        result = split_tarasov(book)
        if len(result.chapters) >= 20:
            return result
    result = split_generic(book)
    if len(result.chapters) > MAX_SPLIT_CHAPTERS:
        raise UploadValidationError(
            f"That file split into too many sections ({len(result.chapters)} chapters).",
            code="unsafe",
        )
    return result


def _match_section(
    text: str, section: tarasov.KnownSection, cursor: int
) -> re.Match[str] | None:
    matches = [
        m
        for candidate in section.titles
        if (m := title_regex(candidate).search(text, cursor)) is not None
    ]
    if not matches:
        return None
    return min(matches, key=lambda m: (m.start(), -m.end()))


def split_tarasov(book: ExtractedBook) -> SplitResult:
    text = paragraphize(book.text)
    found: list[tuple[int, int, tarasov.KnownSection]] = []
    cursor = 0
    skipped: list[str] = []
    for section in tarasov.SECTIONS:
        match = _match_section(text, section, cursor)
        if not match:
            skipped.append(section.title)
            continue
        found.append((match.start(), match.end(), section))
        cursor = match.end()

    # Stop before publisher back-matter if present.
    end_at = len(text)
    for skip_title in SKIP_AFTER_TITLES:
        m = title_regex(skip_title).search(text, found[-1][1] if found else 0)
        if m:
            end_at = min(end_at, m.start())
            break

    chapters: list[ChapterDraft] = []
    for i, (start, title_end, section) in enumerate(found):
        next_start = found[i + 1][0] if i + 1 < len(found) else end_at
        body = paragraphize(text[title_end:next_start])
        body = _strip_leading_part_banner(body)
        if _looks_like_toc(body):
            continue
        if word_count(body) < MIN_CHAPTER_WORDS:
            skipped.append(f"{section.title} (too short)")
            continue
        chapters.append(
            ChapterDraft(
                index=len(chapters),
                title=_clamp(section.title, f"Section {len(chapters) + 1}"),
                body=body,
                part=_clamp_opt(PARTS[section.part_id]),
                source="tarasov",
            )
        )

    title = book.title or "Технология жизни. Книга для героев"
    if "тар" not in (title or "").lower() and "технолог" not in title.lower():
        title = "Технология жизни. Книга для героев"
    return SplitResult(
        title=_clamp(title, "Технология жизни. Книга для героев"),
        author=_clamp_opt(book.author or "Владимир Тарасов"),
        profile="tarasov",
        chapters=chapters,
        skipped=skipped,
    )


def split_generic(book: ExtractedBook) -> SplitResult:
    text = paragraphize(book.text)
    if book.html:
        soup = safe_html_soup(book.html)
        headings = soup.find_all(["h1", "h2", "h3"])
        if len(headings) >= 4:
            html_split = _split_html_headings(book, soup)
            if html_split.chapters:
                return html_split

    chapters = _split_text_headings(text)
    if len(chapters) < 4:
        chapters = _split_short_title_blocks(text)
    if len(chapters) < 2:
        if text and word_count(text) >= MIN_CHAPTER_WORDS:
            chapters = [
                ChapterDraft(
                    index=0,
                    title=_clamp(book.title, "Untitled"),
                    body=text,
                    source="whole-file",
                )
            ]
        else:
            chapters = []
    return SplitResult(
        title=_clamp(book.title or book.filename, "Book"),
        author=_clamp_opt(book.author),
        profile="generic",
        chapters=chapters,
    )


def _split_html_headings(book: ExtractedBook, soup) -> SplitResult:
    chapters: list[ChapterDraft] = []
    current_title = book.title or "Opening"
    current_part = None
    chunks: list[str] = []

    def flush() -> None:
        body = paragraphize("\n".join(chunks))
        if word_count(body) < MIN_CHAPTER_WORDS:
            return
        chapters.append(
            ChapterDraft(
                index=len(chapters),
                title=_clamp(current_title.strip(), f"Section {len(chapters) + 1}"),
                body=body,
                part=_clamp_opt(current_part),
                source="html-heading",
            )
        )

    from bs4 import NavigableString, Tag

    body = soup.body or soup
    for el in body.descendants:
        if isinstance(el, Tag) and el.name in {"h1", "h2", "h3"}:
            flush()
            chunks = []
            heading = el.get_text(" ", strip=True)
            if PART_HEADING.match(heading):
                current_part = heading
                current_title = heading
            else:
                current_title = heading
            continue
        if isinstance(el, NavigableString) and el.parent and el.parent.name not in {"h1", "h2", "h3", "script", "style"}:
            if el.parent.name in {"p", "div", "li", "span"} or el.strip():
                text_val = str(el)
                if text_val.strip():
                    chunks.append(text_val)
    flush()
    if len(chapters) < 3:
        return SplitResult(
            title=_clamp(book.title or book.filename, "Book"),
            author=_clamp_opt(book.author),
            profile="generic",
            chapters=_split_text_headings(book.text),
        )
    return SplitResult(
        title=_clamp(book.title or book.filename, "Book"),
        author=_clamp_opt(book.author),
        profile="generic",
        chapters=chapters,
    )


def _split_text_headings(text: str) -> list[ChapterDraft]:
    lines = text.split("\n")
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if GENERIC_HEADING.match(stripped) or re.match(
            r"^CHAPTER\s+[IVXLCDM0-9]+\.?\s*$", stripped, re.I
        ):
            starts.append((i, re.sub(r"^#+\s*", "", stripped)))
    if len(starts) < 3:
        # Machiavelli-style: CHAPTER N. on one line, title on the next.
        starts = []
        for i, line in enumerate(lines):
            if re.match(r"^CHAPTER\s+[IVXLCDM0-9]+\.?", line.strip(), re.I):
                title_bits = [line.strip()]
                j = i + 1
                while j < len(lines) and lines[j].strip() and not lines[j].strip().endswith("."):
                    if re.match(r"^CHAPTER\s+", lines[j], re.I):
                        break
                    if len(lines[j].strip()) > 90:
                        break
                    title_bits.append(lines[j].strip())
                    j += 1
                    if j > i + 3:
                        break
                starts.append((i, " ".join(title_bits)))
    return _slices_from_line_starts(lines, starts)


def _split_short_title_blocks(text: str) -> list[ChapterDraft]:
    """Split on isolated short title lines — the Tarasov-without-profile fallback."""
    lines = text.split("\n")
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 90:
            continue
        if stripped.endswith((".", "?", "!", ";", ":")):
            continue
        if len(stripped.split()) > 16:
            continue
        prev_blank = i == 0 or not lines[i - 1].strip()
        nxt = next((lines[j] for j in range(i + 1, min(i + 4, len(lines))) if lines[j].strip()), "")
        if prev_blank and nxt and len(nxt) > 40:
            starts.append((i, stripped))
    return _slices_from_line_starts(lines, starts)


def _slices_from_line_starts(lines: list[str], starts: list[tuple[int, str]]) -> list[ChapterDraft]:
    if not starts:
        return []
    chapters: list[ChapterDraft] = []
    part = None
    for i, (line_no, title) in enumerate(starts):
        if PART_HEADING.match(title):
            part = title
        end = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        body_lines = lines[line_no + 1 : end]
        body = paragraphize("\n".join(body_lines))
        if word_count(body) < MIN_CHAPTER_WORDS:
            continue
        if _looks_like_toc(body):
            continue
        clean_title = re.sub(r"^#+\s*", "", title)
        chapters.append(
            ChapterDraft(
                index=len(chapters),
                title=_clamp(clean_title, f"Section {len(chapters) + 1}"),
                body=body,
                part=_clamp_opt(part),
                source="text-heading",
            )
        )
    return chapters


def _looks_like_toc(body: str) -> bool:
    titles = [s.title for s in tarasov.SECTIONS]
    hits = sum(1 for t in titles if normalize_title(t) and normalize_title(t) in normalize_title(body))
    if hits >= 8 and word_count(body) < 400:
        return True
    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    if len(lines) >= 12:
        short = sum(1 for ln in lines if len(ln) < 70 and not ln.endswith("."))
        if short / len(lines) > 0.8:
            return True
    return False


def _strip_leading_part_banner(body: str) -> str:
    lines = body.split("\n")
    while lines and PART_HEADING.match(lines[0].strip()):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and len(lines[0].strip()) < 90 and not lines[0].strip().endswith("."):
            lines.pop(0)
    tail_window = max(0, len(lines) - 12)
    for idx in range(tail_window, len(lines)):
        if PART_HEADING.match(lines[idx].strip()):
            lines = lines[:idx]
            break
    return paragraphize("\n".join(lines))
