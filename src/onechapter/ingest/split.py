from __future__ import annotations

import re
from dataclasses import dataclass, field

from onechapter.ingest import art_of_war, gosudar, tarasov
from onechapter.ingest.extract import (
    MAX_METADATA_CHARS,
    ExtractedBook,
    UploadValidationError,
    _soup_to_paragraphs,
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
    language: str | None = None
    slug: str | None = None


PART_HEADING = re.compile(
    r"^\s*(глава\s+(первая|вторая|третья|четвертая|четвёртая|пятая|[0-9ivxlcdm]+)|chapter\s+[0-9ivxlcdm]+)\b",
    re.IGNORECASE,
)
GENERIC_HEADING = re.compile(
    r"^(#{1,3}\s+.+|глава\s+.+|chapter\s+[ivxlcdm0-9]+[.:].+)$",
    re.IGNORECASE,
)
BARE_CHAPTER_NUM_RE = re.compile(
    r"^(?:глава|chapter)\s+[ivxlcdm0-9]+\.?\s*$",
    re.IGNORECASE,
)
BACK_MATTER_HEADING_RE = re.compile(
    r"^\s*(?:примечания|комментарии)\s*$",
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


def _enforce_max_chapters(result: SplitResult) -> SplitResult:
    if len(result.chapters) > MAX_SPLIT_CHAPTERS:
        raise UploadValidationError(
            f"That file split into too many sections ({len(result.chapters)} chapters).",
            code="unsafe",
        )
    return result


def split_extracted(book: ExtractedBook) -> SplitResult:
    if tarasov.looks_like_tarasov(book.filename, book.text):
        result = split_tarasov(book)
        if len(result.chapters) >= 20:
            return _enforce_max_chapters(result)
    if gosudar.looks_like_gosudar(
        book.filename,
        book.text,
        title=book.title,
        author=book.author,
    ):
        result = split_gosudar(book)
        if len(result.chapters) >= 20:
            return _enforce_max_chapters(result)
    if art_of_war.looks_like_art_of_war(
        book.filename,
        book.text,
        title=book.title,
        author=book.author,
    ):
        result = split_art_of_war(book)
        if len(result.chapters) >= 10:
            return _enforce_max_chapters(result)
    return _enforce_max_chapters(split_generic(book))


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
    for i, (_start, title_end, section) in enumerate(found):
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
        language="ru",
        slug="tehnologiya-zhizni",
    )


def _consume_subtitle_lines(lines: list[str], after_idx: int) -> tuple[str, int]:
    """Collect 1-3 subtitle lines immediately following a bare 'ГЛАВА N' / 'CHAPTER N' line."""
    j = after_idx
    while j < len(lines) and not lines[j].strip():
        j += 1
    subtitle_bits: list[str] = []
    while j < len(lines):
        stripped = lines[j].strip()
        if not stripped:
            break
        if gosudar.CHAPTER_HEADING_RE.match(stripped) or re.match(r"^chapter\s+", stripped, re.I):
            break
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        letters = [c for c in stripped if c.isalpha()]
        is_all_caps = bool(letters) and all(c.isupper() for c in letters)
        if len(stripped) > 160 or len(stripped.split()) > 24:
            break
        if not is_all_caps and stripped.endswith(".") and subtitle_bits:
            break
        if not is_all_caps and stripped.endswith(".") and len(stripped) > 95:
            break
        subtitle_bits.append(stripped)
        j += 1
        if len(subtitle_bits) >= 3:
            break
    return " ".join(subtitle_bits), j


def _find_back_matter_line(lines: list[str], start_line: int, titles: tuple[str, ...]) -> int:
    norm_targets = {normalize_title(t) for t in titles if normalize_title(t)}
    for idx in range(start_line, len(lines)):
        stripped = lines[idx].strip()
        if not stripped or len(stripped) > 60:
            continue
        norm = normalize_title(stripped)
        if norm in norm_targets:
            prev_blank = idx == 0 or not lines[idx - 1].strip()
            if prev_blank:
                return idx
    return len(lines)


def split_gosudar(book: ExtractedBook) -> SplitResult:
    text = paragraphize(book.text)
    lines = text.split("\n")
    skipped: list[str] = []

    raw_candidates: list[tuple[int, int, str, str]] = []
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        m = gosudar.CHAPTER_HEADING_RE.match(stripped) if stripped else None
        if not m:
            idx += 1
            continue
        roman = gosudar.parse_chapter_numeral(m.group(1))
        if not roman:
            idx += 1
            continue
        inline_sub = (m.group(2) or "").strip()
        if inline_sub:
            subtitle_raw = inline_sub
            body_start = idx + 1
        else:
            subtitle_raw, body_start = _consume_subtitle_lines(lines, idx + 1)
        raw_candidates.append((idx, body_start, roman, subtitle_raw))
        idx = max(idx + 1, body_start)

    valid_chapters: list[tuple[int, int, int, str, str]] = []
    for c_i, (head_line, body_start, roman, subtitle_raw) in enumerate(raw_candidates):
        next_head = (
            raw_candidates[c_i + 1][0]
            if c_i + 1 < len(raw_candidates)
            else _find_back_matter_line(lines, body_start, gosudar.SKIP_AFTER_TITLES)
        )
        for scan_i in range(body_start, next_head):
            s = lines[scan_i].strip()
            if s and len(s) <= 100 and (
                gosudar.DEDICATION_HEADING_RE.match(s)
                or gosudar.FRONT_EDITORIAL_HEADING_RE.match(s)
            ):
                prev_blank = scan_i == 0 or not lines[scan_i - 1].strip()
                if prev_blank:
                    next_head = scan_i
                    break
        probe_body = paragraphize("\n".join(lines[body_start:next_head]))
        if word_count(probe_body) < 25 or _looks_like_toc(probe_body):
            continue
        valid_chapters.append((head_line, body_start, next_head, roman, subtitle_raw))

    # Select a strictly increasing line-order chain of chapters I..XXVI.
    candidates_by_roman: dict[str, list[tuple[int, int, int, str, str]]] = {}
    for entry in valid_chapters:
        candidates_by_roman.setdefault(entry[3], []).append(entry)

    ordered_entries: list[tuple[int, int, int, str, str]] = []
    cursor_line = -1
    for r in gosudar.ROMAN_NUMERALS:
        options = [e for e in candidates_by_roman.get(r, []) if e[0] > cursor_line]
        if not options:
            skipped.append(f"Глава {r}")
            continue
        chosen = options[0]
        ordered_entries.append(chosen)
        cursor_line = chosen[0]

    chapters: list[ChapterDraft] = []
    if ordered_entries:
        first_ch_line = ordered_entries[0][0]
        last_body_start = ordered_entries[-1][1]
        back_cutoff = _find_back_matter_line(lines, last_body_start, gosudar.SKIP_AFTER_TITLES)
        stripped_endnotes = back_cutoff < len(lines)

        # Locate the Dedication before Chapter I, ignoring TOC lines or editorial prefaces.
        ded_candidates: list[tuple[int, str]] = []
        for line_i in range(0, first_ch_line):
            s = lines[line_i].strip()
            if s and gosudar.DEDICATION_HEADING_RE.match(s):
                ded_candidates.append((line_i, s))

        for ded_line, ded_heading in reversed(ded_candidates):
            ded_end = first_ch_line
            for scan_i in range(ded_line + 1, first_ch_line):
                s = lines[scan_i].strip()
                if s and gosudar.FRONT_EDITORIAL_HEADING_RE.match(s) and len(s) <= 80:
                    prev_blank = scan_i == 0 or not lines[scan_i - 1].strip()
                    if prev_blank:
                        ded_end = scan_i
                        break
            ded_prose = gosudar.clean_prose_body(
                "\n".join(lines[ded_line + 1 : ded_end]),
                strip_endnote_refs=stripped_endnotes,
            )
            if word_count(ded_prose) >= 60 and not _looks_like_toc(ded_prose):
                if "лоренцо" in ded_heading.lower():
                    ded_body = f"{gosudar.CANONICAL_SALUTATION}\n\n{ded_prose}"
                else:
                    ded_body = ded_prose
                chapters.append(
                    ChapterDraft(
                        index=0,
                        title=_clamp(gosudar.CANONICAL_DEDICATION_TITLE, "Посвящение"),
                        body=ded_body,
                        part=_clamp_opt(gosudar.CANONICAL_PART),
                        source="prince_ru",
                    )
                )
                break
        else:
            skipped.append(gosudar.CANONICAL_DEDICATION_TITLE)

        for e_i, (_head_line, body_start, _probe_end, roman, subtitle_raw) in enumerate(
            ordered_entries
        ):
            ch_end = (
                ordered_entries[e_i + 1][0]
                if e_i + 1 < len(ordered_entries)
                else back_cutoff
            )
            body = gosudar.clean_prose_body(
                "\n".join(lines[body_start:ch_end]),
                strip_endnote_refs=stripped_endnotes,
            )
            if word_count(body) < 25:
                skipped.append(f"Глава {roman} (too short)")
                continue
            subtitle = gosudar.format_russian_subtitle(subtitle_raw)
            full_title = f"Глава {roman}. {subtitle}" if subtitle else f"Глава {roman}"
            chapters.append(
                ChapterDraft(
                    index=len(chapters),
                    title=_clamp(full_title, f"Глава {roman}"),
                    body=body,
                    part=_clamp_opt(gosudar.CANONICAL_PART),
                    source="prince_ru",
                )
            )

    return SplitResult(
        title=_clamp(gosudar.CANONICAL_TITLE, "Государь"),
        author=_clamp_opt(gosudar.CANONICAL_AUTHOR),
        profile="prince",
        chapters=chapters,
        skipped=skipped,
        language="ru",
        slug=gosudar.CANONICAL_SLUG,
    )


def _find_art_of_war_back_cutoff(lines: list[str], start_line: int) -> int:
    cutoff = _find_back_matter_line(lines, start_line, art_of_war.SKIP_AFTER_TITLES)
    for idx in range(start_line, cutoff):
        stripped = lines[idx].strip()
        if not stripped:
            continue
        prev_blank = idx == 0 or not lines[idx - 1].strip()
        if not prev_blank:
            continue
        if art_of_war.COMMENTARY_START_RE.match(stripped):
            return idx
        m = art_of_war.CHAPTER_HEADING_RE.match(stripped)
        if m:
            num = art_of_war.parse_chapter_number(m.group(1))
            if num == 1 or not art_of_war.is_plausible_inline_subtitle(m.group(2) or ""):
                return idx
    return cutoff


def split_art_of_war(book: ExtractedBook) -> SplitResult:
    text = paragraphize(book.text)
    lines = text.split("\n")
    skipped: list[str] = []

    raw_candidates: list[tuple[int, int, int, str]] = []
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        m = art_of_war.CHAPTER_HEADING_RE.match(stripped) if stripped else None
        if not m:
            idx += 1
            continue
        num = art_of_war.parse_chapter_number(m.group(1))
        if not num:
            idx += 1
            continue
        inline_sub = (m.group(2) or "").strip()
        if inline_sub:
            if not art_of_war.is_plausible_inline_subtitle(inline_sub):
                idx += 1
                continue
            subtitle_raw = inline_sub
            body_start = idx + 1
        else:
            subtitle_raw, body_start = _consume_subtitle_lines(lines, idx + 1)
        raw_candidates.append((idx, body_start, num, subtitle_raw))
        idx = max(idx + 1, body_start)

    valid_chapters: list[tuple[int, int, int, int, str]] = []
    for c_i, (head_line, body_start, num, subtitle_raw) in enumerate(raw_candidates):
        next_head = (
            raw_candidates[c_i + 1][0]
            if c_i + 1 < len(raw_candidates)
            else _find_art_of_war_back_cutoff(lines, body_start)
        )
        probe_body = paragraphize("\n".join(lines[body_start:next_head]))
        if word_count(probe_body) < 25 or _looks_like_toc(probe_body):
            continue
        valid_chapters.append((head_line, body_start, next_head, num, subtitle_raw))

    candidates_by_num: dict[int, list[tuple[int, int, int, int, str]]] = {}
    for entry in valid_chapters:
        candidates_by_num.setdefault(entry[3], []).append(entry)

    ordered_entries: list[tuple[int, int, int, int, str]] = []
    cursor_line = -1
    for num in art_of_war.CHAPTER_NUMBERS:
        options = [e for e in candidates_by_num.get(num, []) if e[0] > cursor_line]
        if not options:
            skipped.append(f"Глава {num}")
            continue
        chosen = options[0]
        ordered_entries.append(chosen)
        cursor_line = chosen[0]

    chapters: list[ChapterDraft] = []
    if ordered_entries:
        last_body_start = ordered_entries[-1][1]
        back_cutoff = _find_art_of_war_back_cutoff(lines, last_body_start)
        stripped_notes = back_cutoff < len(lines)

        for e_i, (_head_line, body_start, _probe_end, num, subtitle_raw) in enumerate(
            ordered_entries
        ):
            ch_end = (
                ordered_entries[e_i + 1][0]
                if e_i + 1 < len(ordered_entries)
                else back_cutoff
            )
            body = art_of_war.clean_prose_body(
                "\n".join(lines[body_start:ch_end]),
                strip_note_refs=stripped_notes,
            )
            if word_count(body) < 25:
                skipped.append(f"Глава {num} (too short)")
                continue
            subtitle = art_of_war.clean_subtitle(subtitle_raw)
            full_title = f"Глава {num}. {subtitle}" if subtitle else f"Глава {num}"
            chapters.append(
                ChapterDraft(
                    index=len(chapters),
                    title=_clamp(full_title, f"Глава {num}"),
                    body=body,
                    part=_clamp_opt(art_of_war.CANONICAL_PART),
                    source="art_of_war_ru",
                )
            )

    return SplitResult(
        title=_clamp(art_of_war.CANONICAL_TITLE, "Искусство войны"),
        author=_clamp_opt(art_of_war.CANONICAL_AUTHOR),
        profile="art_of_war",
        chapters=chapters,
        skipped=skipped,
        language="ru",
        slug=art_of_war.CANONICAL_SLUG,
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
        language=book.language,
    )


def _split_html_headings(book: ExtractedBook, soup) -> SplitResult:
    from bs4 import Tag

    chapters: list[ChapterDraft] = []
    current_title = book.title or "Opening"
    current_part = None
    fragment_parts: list[str] = []

    def flush() -> None:
        if not fragment_parts:
            return
        frag_soup = safe_html_soup("".join(fragment_parts))
        body = _soup_to_paragraphs(frag_soup)
        if word_count(body) < MIN_CHAPTER_WORDS or _looks_like_toc(body):
            return
        if BACK_MATTER_HEADING_RE.match(current_title.strip()):
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

    for tag in soup.find_all(["script", "style", "nav"]):
        tag.decompose()
    root = soup.body or soup

    def _walk(node) -> None:
        nonlocal current_title, current_part, fragment_parts
        for child in getattr(node, "children", []):
            if isinstance(child, Tag) and child.name in {"h1", "h2", "h3"}:
                flush()
                fragment_parts = []
                heading = child.get_text(" ", strip=True)
                if PART_HEADING.match(heading):
                    current_part = heading
                    current_title = heading
                else:
                    current_title = heading
            elif isinstance(child, Tag) and child.find(["h1", "h2", "h3"]):
                _walk(child)
            else:
                fragment_parts.append(str(child))

    _walk(root)
    flush()
    if len(chapters) < 3:
        return SplitResult(
            title=_clamp(book.title or book.filename, "Book"),
            author=_clamp_opt(book.author),
            profile="generic",
            chapters=_split_text_headings(book.text),
            language=book.language,
        )
    return SplitResult(
        title=_clamp(book.title or book.filename, "Book"),
        author=_clamp_opt(book.author),
        profile="generic",
        chapters=chapters,
        language=book.language,
    )


def _split_text_headings(text: str) -> list[ChapterDraft]:
    lines = text.split("\n")
    end_limit = _find_back_matter_line(lines, 0, ("Примечания", "Комментарии"))
    lines = lines[:end_limit]
    starts: list[tuple[int, int, str]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if BARE_CHAPTER_NUM_RE.match(stripped):
            subtitle, body_start = _consume_subtitle_lines(lines, i + 1)
            head = stripped.rstrip(".")
            full_title = f"{head}. {subtitle}" if subtitle else stripped
            starts.append((i, body_start, full_title))
            i = max(i + 1, body_start)
            continue
        if GENERIC_HEADING.match(stripped):
            clean_head = re.sub(r"^#+\s*", "", stripped)
            starts.append((i, i + 1, clean_head))
        i += 1
    return _slices_from_line_starts(lines, starts)


def _split_short_title_blocks(text: str) -> list[ChapterDraft]:
    """Split on isolated short title lines — the Tarasov-without-profile fallback."""
    lines = text.split("\n")
    starts: list[tuple[int, int, str]] = []
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
            starts.append((i, i + 1, stripped))
    return _slices_from_line_starts(lines, starts)


def _slices_from_line_starts(
    lines: list[str],
    starts: list[tuple[int, int, str]],
) -> list[ChapterDraft]:
    if not starts:
        return []
    chapters: list[ChapterDraft] = []
    part = None
    for i, (_line_no, body_start, title) in enumerate(starts):
        if PART_HEADING.match(title) and not BARE_CHAPTER_NUM_RE.match(title.split(".")[0]):
            part = title
        end = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        body_lines = lines[body_start:end]
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
