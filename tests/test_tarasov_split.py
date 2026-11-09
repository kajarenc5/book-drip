from __future__ import annotations

from pathlib import Path

from onechapter.ingest.extract import ExtractedBook
from onechapter.ingest.split import split_extracted, split_tarasov
from onechapter.ingest.tarasov import SECTIONS


def _tarasov_text(omit: set[str] | None = None) -> str:
    omit = omit or set()
    parts = [
        "Владимир Тарасов",
        "Технология жизни. Книга для героев",
        "",
        "Оглавление",
        "Предисловие",
        "Смысл всякой деятельности лежит вне ее пределов",
        "Выходи за пределы жизни",
        "Человек, который кормил обезьян",
        "",
        "Предисловие к третьему изданию",
        "",
        "Третье издание этой книги отличается от первых двух небольшими изменениями.",
        "Автор благодарит читателей предыдущих изданий за поддержку и внимание к тексту.",
        "",
        "Предисловие",
        "",
        "Вы держите в руках книгу, которая, возможно, изменит вашу жизнь.",
        "Большинство мыслей, которые вы здесь найдете, не принадлежат мне.",
        "",
        "Глава первая. Путь",
        "Сначала мы выбираем путь, потом он выбирает нас",
        "",
        "Смысл всякой деятельности лежит вне ее пределов",
        "",
        "Чтобы понять смысл деятельности, нужно выйти за ее пределы.",
        "Внутри нее мы можем понять только смысл ее отдельных частей.",
        "И смысл жизни — вне ее пределов.",
        "",
        "Выходи за пределы жизни",
        "",
        "Жили три брата. Первый никогда не унывал. Второй ничего не делал зря.",
        "Третий всегда делал то, что требовалось. Тогда надо выходить за пределы жизни.",
        "",
        "Человек, который кормил обезьян",
        "",
        "Человек, который кормил обезьян орехами, однажды сказал, что орехов стало мало.",
        "Обезьяны пришли в ярость, а потом обрадовались. Человек этот давно умер.",
        "",
        "Большая цель достижима",
        "",
        "Большая цель — это цель, к которой можно прийти. Но только после смерти.",
        "Маленькой цели можно достичь и при жизни, если очень постараться каждый день.",
        "",
        "Наметь дату своей смерти",
        "",
        "Человек написал дату своей смерти. Далекая она, эта дата, потому что он должен быть уверен.",
        "",
    ]
    written = {
        "Предисловие к третьему изданию",
        "Предисловие",
        "Смысл всякой деятельности лежит вне ее пределов",
        "Выходи за пределы жизни",
        "Человек, который кормил обезьян",
        "Большая цель достижима",
        "Наметь дату своей смерти",
    }
    remaining = [s.title for s in SECTIONS if s.title not in written]
    for title in remaining:
        if title in omit:
            continue
        parts.extend([title, "", f"Тестовый абзац для раздела «{title}». " * 8, ""])
    parts.extend(["Об авторе", "Владимир Тарасов — основатель Таллиннской школы менеджеров."])
    return "\n".join(parts)


def test_tarasov_profile_splits_short_essays_not_parts():
    book = ExtractedBook(
        filename="vladimir_tarasov_tehnologiya_zhi.mobi",
        text=_tarasov_text(),
        title="Технология жизни",
        author="Владимир Тарасов",
    )
    result = split_tarasov(book)
    titles = [ch.title for ch in result.chapters]
    assert result.profile == "tarasov"
    assert "Смысл всякой деятельности лежит вне ее пределов" in titles
    assert "Человек, который кормил обезьян" in titles
    assert "За пределами жизни" in titles
    assert all("Глава первая" not in ch.title for ch in result.chapters)
    monkey = next(ch for ch in result.chapters if ch.title.startswith("Человек, который"))
    assert "орехами" in monkey.body
    assert "Оглавление" not in monkey.body
    assert monkey.part == "Путь"
    assert len(result.chapters) >= 70


def test_detects_tarasov_from_filename():
    book = ExtractedBook(filename="vladimir_tarasov_tehnologiya_zhi.mobi", text=_tarasov_text())
    result = split_extracted(book)
    assert result.profile == "tarasov"


def test_ingest_stores_tarasov_under_stable_slug(data_dir):
    from onechapter.db import init_db
    from onechapter.library import ingest_file

    init_db()
    path = data_dir / "vladimir_tarasov_tehnologiya_zhi.txt"
    path.write_text(_tarasov_text(), encoding="utf-8")

    book = ingest_file(path)

    assert book["slug"] == "tehnologiya-zhizni"
    assert book["language"] == "ru"
    assert book["profile"] == "tarasov"
    assert book["chapter_count"] >= 70


def test_skips_missing_edition_title():
    book = ExtractedBook(
        filename="tarasov.mobi",
        text=_tarasov_text(omit={"Поднебесная не стоит того"}),
    )
    result = split_tarasov(book)
    titles = [ch.title for ch in result.chapters]
    assert "Поднебесная не стоит того" not in titles
    assert "Дели отряды для грабежа" in titles


def test_handles_first_edition_title_variants_without_skipping_parts():
    raw = (
        _tarasov_text()
        .replace("Выходи за пределы жизни", "Выходите за пределы жизни")
        .replace("Большая цель достижима", "Большую цель можно достигнуть")
        .replace("Наметь дату своей смерти", "Наметьте дату своей смерти")
        .replace("Радость неудачи", "Радости неудачи")
        .replace("За пределами жизни", "ЗАКЛЮЧЕНИЕ")
    )
    book = ExtractedBook(filename="vladimir_tarasov_tehnologiya_zhi.mobi", text=raw)
    result = split_tarasov(book)
    titles = [ch.title for ch in result.chapters]
    assert "Радость неудачи" in titles
    assert "Он придет и скажет" in titles
    assert "За пределами жизни" in titles
    assert len(result.chapters) >= 70
