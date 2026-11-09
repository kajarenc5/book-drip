from __future__ import annotations

import json
import re
from pathlib import Path

from onechapter.config import SEED_DIR
from onechapter.ingest.split import ChapterDraft, SplitResult
from onechapter.ingest.textutil import paragraphize, word_count
from onechapter.library import upsert_book_from_split


SLOW_READING = SplitResult(
    title="A Note on Slow Reading",
    author="One Chapter",
    profile="builtin",
    chapters=[
        ChapterDraft(
            0,
            "One page a day",
            "Some books are not meant to be finished. They are meant to be entered.\n\n"
            "A short chapter with dense meaning will not yield itself to a commute, "
            "or to the last ten minutes before sleep. It asks for a day of company: "
            "to walk around in you while you cook, while you wait, while you talk to someone else.\n\n"
            "This companion exists for that kind of book. One chapter. Then a full day "
            "before the next.",
            part="How to begin",
            source="builtin",
        ),
        ChapterDraft(
            1,
            "Density is the point",
            "If a chapter is small, the temptation is to take another. That is how a book "
            "of parables becomes a book you have 'read' and cannot remember.\n\n"
            "Leave it small. Let one image, one rule, one story be enough. Tomorrow's "
            "chapter will still be there. Today's will not come back in the same way "
            "if you rush past it.",
            part="How to begin",
            source="builtin",
        ),
        ChapterDraft(
            2,
            "Do not skip ahead",
            "The next chapter is locked on purpose. Not as a game, and not as discipline "
            "for its own sake. A sequence of dense thoughts is a sequence: the later ones "
            "assume you have lived with the earlier ones.\n\n"
            "You may reread today. You may return to yesterday. You may not borrow from tomorrow.",
            part="How to begin",
            source="builtin",
        ),
        ChapterDraft(
            3,
            "Tomorrow will wait",
            "If you miss a morning, the book does not punish you with two chapters. "
            "It holds the missed one open in the archive, and still gives you only one "
            "new page when the next day arrives.\n\n"
            "Catch up when you want, slowly. The practice is the pace.",
            part="How to begin",
            source="builtin",
        ),
        ChapterDraft(
            4,
            "Begin",
            "When you are ready, open the library and start Vladimir Tarasov's "
            "Технология жизни — or another book of short, heavy chapters.\n\n"
            "Drop the file you already own. The companion will cut it at the titled essays, "
            "not at the four large parts, and will send you the first one today.\n\n"
            "Then stop. Think. Wait for tomorrow.",
            part="How to begin",
            source="builtin",
        ),
    ],
)


def seed_builtin_books(conn=None) -> None:
    from onechapter.db import init_db
    from onechapter.reader.models import Book

    init_db()
    if not Book.objects.filter(slug="slow-reading").exists():
        upsert_book_from_split(
            conn,
            SLOW_READING,
            slug="slow-reading",
            filename="builtin:slow-reading",
            language="en",
        )
    prince_json = SEED_DIR / "the-prince.json"
    if prince_json.exists() and not Book.objects.filter(slug="the-prince").exists():
        upsert_book_from_split(
            conn,
            load_split_json(prince_json),
            slug="the-prince",
            filename="gutenberg:1232",
            language="en",
        )


def load_split_json(path: Path) -> SplitResult:
    data = json.loads(path.read_text(encoding="utf-8"))
    chapters = [
        ChapterDraft(
            index=i,
            title=ch["title"],
            body=ch["body"],
            part=ch.get("part"),
            source=ch.get("source", "seed"),
        )
        for i, ch in enumerate(data["chapters"])
    ]
    return SplitResult(
        title=data["title"],
        author=data.get("author"),
        profile=data.get("profile", "seed"),
        chapters=chapters,
    )


def parse_gutenberg_prince(text: str) -> SplitResult:
    start = text.find("To the Magnificent Lorenzo")
    if start < 0:
        raise ValueError("Could not find the Dedication in this Gutenberg text.")
    heading = text.rfind("DEDICATION", 0, start)
    if heading != -1 and start - heading < 80:
        start = heading
    xxvi = text.find("CHAPTER XXVI", start)
    end = len(text)
    if xxvi != -1:
        for marker in (
            "DESCRIPTION OF THE METHODS ADOPTED BY THE DUKE VALENTINO",
            "THE LIFE OF CASTRUCCIO",
            "*** END OF THE PROJECT GUTENBERG",
        ):
            at = text.find(marker, xxvi + 1)
            if at != -1:
                end = min(end, at)
    body = text[start:end]
    pattern = re.compile(r"^CHAPTER\s+([IVXLCDM]+)\.(?:\[\d+\])?\s*$", re.MULTILINE)
    matches = list(pattern.finditer(body))
    chapters: list[ChapterDraft] = []

    dedication_end = matches[0].start() if matches else len(body)
    dedication = paragraphize(body[:dedication_end])
    dedication = re.sub(r"^DEDICATION\s*", "", dedication).strip()
    dedication = re.sub(r"\nTHE PRINCE\s*$", "", dedication).strip()
    chapters.append(
        ChapterDraft(
            index=0,
            title="Dedication to Lorenzo de' Medici",
            body=dedication,
            part="The Prince",
            source="gutenberg",
        )
    )

    for i, match in enumerate(matches):
        chunk_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = body[match.end() : chunk_end]
        lines = chunk.splitlines()
        title_lines: list[str] = []
        rest_index = 0
        for j, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                if title_lines:
                    rest_index = j
                    break
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                rest_index = j
                break
            if len(stripped) > 90 and title_lines:
                rest_index = j
                break
            title_lines.append(stripped)
            rest_index = j + 1
            if j >= 3:
                break
        title = " ".join(title_lines)
        title = re.sub(r"\s+", " ", title).strip(" .")
        numeral = match.group(1)
        full_title = f"Chapter {numeral}. {title}" if title else f"Chapter {numeral}"
        chapter_body = paragraphize("\n".join(lines[rest_index:]))
        chapter_body = re.sub(r"^THE PRINCE\s*", "", chapter_body).strip()
        chapters.append(
            ChapterDraft(
                index=len(chapters),
                title=full_title,
                body=chapter_body,
                part="The Prince",
                source="gutenberg",
            )
        )

    return SplitResult(
        title="The Prince",
        author="Niccolò Machiavelli",
        profile="prince",
        chapters=chapters,
    )


def write_prince_seed(source: Path, dest: Path) -> None:
    split = parse_gutenberg_prince(source.read_text(encoding="utf-8"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "title": split.title,
                "author": split.author,
                "profile": split.profile,
                "license": "Public domain. Source: Project Gutenberg ebook 1232, translated by W. K. Marriott.",
                "chapters": [
                    {
                        "title": ch.title,
                        "body": ch.body,
                        "part": ch.part,
                        "source": ch.source,
                        "word_count": word_count(ch.body),
                    }
                    for ch in split.chapters
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
