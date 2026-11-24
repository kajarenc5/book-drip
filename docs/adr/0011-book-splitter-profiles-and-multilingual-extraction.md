# ADR 0011: Book-specific splitter profiles and multilingual Cyrillic extraction

- Status: Accepted (implemented in PR #1, PR #5, PR #10, and updated 2026-11-24: `src/onechapter/ingest/tarasov.py`, `src/onechapter/ingest/gosudar.py`, `src/onechapter/ingest/art_of_war.py`, `src/onechapter/ingest/split.py`, `src/onechapter/ingest/extract.py`, `src/onechapter/ingest/textutil.py`, `src/onechapter/library.py`, `src/onechapter/reader/views.py`, `tests/test_tarasov_split.py`, `tests/test_gosudar_split.py`, `tests/test_art_of_war_split.py`)
- Date: 2026-11-18

## Context

One Chapter's core value is splitting dense books into true daily units rather than arbitrary raw file chunks or publisher parts. Generic heading splitters break down on classic and Russian editions for three reasons:

1. **Editions vary in structure and editorial framing:**
   - Vladimir Tarasov's *Технология жизни. Книга для героев* contains ~78 short titled essays grouped inside 4 large parts, with alternate imperative/plural titles across printings and publisher front/back matter.
   - Russian editions of Machiavelli's *Государь* (*The Prince*) wrap the 27-unit treatise (Dedication + Chapters I–XXVI) with translator prefaces, tables of contents, ALL-CAPS subtitles, ornamental divider lines, and endnotes (`Примечания` / `Комментарии`), and must coexist in the shared library alongside the seeded English `the-prince`.
2. **Naive HTML/FB2 text extraction breaks paragraphs:** Flattening inline tags (`<a>`, `<em>`, `<strong>`, `<emphasis>`) or block boundaries in FB2/EPUB/HTML caused mid-sentence line breaks and awkward Telegram/web formatting.
3. **Non-ASCII titles and typography:** Cyrillic titles needed clean ASCII slugs for URLs and `/read <slug>` commands, plus accurate HTML `lang="ru"` / `lang="en"` attributes for browser typography and hyphenation.

## Decision

1. **Profile-Driven Book Splitting (`ingest/split.py`):**
   - Maintain dedicated book profiles detected automatically from filename, metadata, and body heuristics, falling back to the generic heading/chunk splitter when no profile matches:
     - **`tarasov` (`ingest/tarasov.py`):** Matches publisher essay titles across printings (including first-edition variants without cutting inside prose paragraphs), skips missing edition titles gracefully, drops *Об авторе* / publisher catalogue, and locks the book to canonical slug `tehnologiya-zhizni` (`language="ru"`).
     - **`gosudar` (`ingest/gosudar.py`):** Detects Russian Machiavelli editions (*Государь*), isolates strictly the Dedication (`Посвящение Лоренцо деи Медичи`) and Chapters I–XXVI (accepting Roman or Arabic numerals), normalizes ALL-CAPS Russian subtitles while restoring proper nouns, strips editorial prefaces, ornament lines, and endnote reference markers (`[1]`, `*[2]`), drops back matter (`Примечания`, `Комментарии`, etc.), and locks to canonical slug `gosudar` (`language="ru"`) so it coexists cleanly with English `the-prince`.
     - **`art_of_war` (`ingest/art_of_war.py`):** Detects Russian Sun Tzu editions (*Искусство войны*, e.g., N. I. Konrad's translation), isolates strictly Chapters 1–13 (accepting Arabic or Roman numerals), strips translator prefaces, heading/body footnote markers (`[1]`, `[*]`, `[**]`, `[***]`) while preserving bracketed prose insertions, drops chapter-by-chapter endnote commentaries (`Глава 1[*]...`, `1.1. ...`, `Примечания`, `Комментарии`), and locks to canonical slug `iskusstvo-voyny` (`language="ru"`).
     - **`generic`:** Generic chapter/heading splitter for arbitrary user-uploaded books.

2. **Paragraph-Preserving HTML/FB2 Extraction (`ingest/extract.py`):**
   - Distinguish inline markup tags (`a`, `em`, `strong`, `emphasis`, `i`, `b`, `cite`, etc.) from block-level elements during HTML/XHTML and FB2 extraction so inline links and emphasis remain seamless within paragraphs while paragraph breaks (`\n\n`) stay intact.

3. **ASCII Slug Transliteration & Language Detection (`ingest/textutil.py`, `reader/views.py`):**
   - Transliterate Cyrillic and accented titles into clean ASCII slugs (`slugify_ascii`) for URLs and Telegram `/read <slug>` commands.
   - Automatically detect and normalize book language (`detect_language`, `normalize_lang_code`, stored on `Book.language`) and expose it in web templates (`<article lang="...">`) for proper typography and hyphenation.

## Consequences

- Both *Технология жизни* and Russian *Государь* ingest into clean, canonical daily chapters regardless of minor publisher/edition differences, and Russian *Государь* (`gosudar`) coexists alongside English *The Prince* (`the-prince`).
- Adding future canonical profiles (e.g., *Meditations*, *Enchiridion*, *Art of War*) follows a clear, self-contained module pattern under `src/onechapter/ingest/` with targeted unit tests and operator-safe `reprocess` support ([ADR 0005](0005-s3-book-originals.md), [ADR 0007](0007-extensible-reflections-and-media.md)).
- Web and Telegram prose renders with clean paragraphs and accurate Cyrillic language metadata.

## Alternatives considered

- **Use only a generic heading regex for all books.** Fails on multi-part essays (splits Tarasov into 4 giant parts instead of ~78 essays) and leaks translator prefaces, tables of contents, and endnotes into day 1 or trailing chapters.
- **Require readers to manually edit or clean uploaded books.** Adds friction and conflicts with the immutable shared library model ([ADR 0005](0005-s3-book-originals.md), [ADR 0006](0006-unified-identity-and-access-control.md)).
