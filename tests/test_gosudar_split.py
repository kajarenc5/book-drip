from __future__ import annotations

from django.test import Client

from onechapter.ingest.extract import ExtractedBook, _from_html, extract_path
from onechapter.ingest.gosudar import ROMAN_NUMERALS
from onechapter.ingest.split import split_extracted, split_gosudar
from onechapter.ingest.textutil import detect_language, slugify_ascii


def _synthetic_gosudar_text(*, include_front_noise: bool = True, include_notes: bool = True) -> str:
    parts: list[str] = []
    if include_front_noise:
        parts.extend(
            [
                "Издательство Классика, серия Политика и история",
                "",
                "Вступительная статья",
                "",
                ("Биография флорентийского секретаря и обзор эпохи Возрождения в Италии. " * 10).strip(),
                "",
                "Оглавление",
                "",
                "Николо Макиавелли - его светлости Лоренцо деи Медичи",
                "Глава I. Скольких видов бывают государства",
                "Глава II. О наследственном единовластии",
                "Глава III. О смешанных государствах",
                "",
            ]
        )

    parts.extend(
        [
            "Николо Макиавелли - его светлости Лоренцо деи Медичи",
            "",
            "* * *",
            "",
            (
                "Синтетический первый абзац посвящения правителю Флоренции о многолетнем опыте "
                "наблюдения за государственными делами и чтении древних историков. " * 4
            ).strip(),
            "",
            (
                "Синтетический второй абзац посвящения с пожеланием величия и упоминанием превратностей судьбы. "
                * 4
            ).strip(),
            "",
        ]
    )

    sample_subtitles = {
        "I": "СКОЛЬКИХ ВИДОВ БЫВАЮТ ГОСУДАРСТВА И КАК ОНИ ПРИОБРЕТАЮТСЯ",
        "IV": "ПОЧЕМУ ЦАРСТВО ДАРИЯ, ЗАВОЕВАННОЕ АЛЕКСАНДРОМ, НЕ ВОССТАЛО ПРОТИВ ПРЕЕМНИКОВ АЛЕКСАНДРА ПОСЛЕ ЕГО СМЕРТИ",
        "XXIV": "ПОЧЕМУ ГОСУДАРИ ИТАЛИИ ЛИШИЛИСЬ СВОИХ ГОСУДАРСТВ",
        "XXVI": "ПРИЗЫВ ОВЛАДЕТЬ ИТАЛИЕЙ И ОСВОБОДИТЬ ЕЕ ИЗ РУК ВАРВАРОВ",
    }

    for roman in ROMAN_NUMERALS:
        subtitle = sample_subtitles.get(roman, f"ТЕСТОВЫЙ ЗАГОЛОВОК ДЛЯ РАЗДЕЛА {roman}")
        note_marker = ' "exemplum latinum".* [1]. Продолжение фразы.' if roman in {"VI", "XXVI"} else ""
        parts.extend(
            [
                f"ГЛАВА {roman}",
                "",
                subtitle,
                "",
                (f"Первый учебный абзац рассуждения для главы {roman}.{note_marker} " * 4).strip(),
                "",
                (f"Второй учебный абзац рассуждения для главы {roman} о доблести и законах. " * 4).strip(),
                "",
            ]
        )

    if include_notes:
        parts.extend(
            [
                "Примечания",
                "",
                "1",
                "",
                "exemplum latinum — перевод латинского выражения в редакторском комментарии.",
                "",
                "Комментарии",
                "",
                "Дополнительная справочная заметка редактора издания.",
            ]
        )
    return "\n".join(parts)


def test_gosudar_profile_splits_dedication_and_26_chapters_without_front_or_back_matter():
    book = ExtractedBook(
        filename="nikkolo_makiavelli-gosudar-1493132446.mobi",
        text=_synthetic_gosudar_text(),
        title="Государь",
        author="Николо Макиавелли",
        language="ru",
    )
    result = split_extracted(book)

    assert result.profile == "prince"
    assert result.slug == "gosudar"
    assert result.language == "ru"
    assert result.title == "Государь"
    assert result.author == "Никколо Макиавелли"
    assert len(result.chapters) == 27
    assert result.skipped == []

    dedication = result.chapters[0]
    assert dedication.title == "Посвящение Лоренцо деи Медичи"
    assert dedication.part == "Государь"
    assert dedication.body.startswith("Никколо Макиавелли — его светлости Лоренцо деи Медичи\n\n")
    assert "* * *" not in dedication.body
    assert "Вступительная статья" not in dedication.body
    assert "Биография флорентийского секретаря" not in dedication.body
    assert "Оглавление" not in dedication.body

    assert result.chapters[1].title == (
        "Глава I. Скольких видов бывают государства и как они приобретаются"
    )
    assert result.chapters[4].title == (
        "Глава IV. Почему царство Дария, завоеванное Александром, "
        "не восстало против преемников Александра после его смерти"
    )
    assert result.chapters[24].title == (
        "Глава XXIV. Почему государи Италии лишились своих государств"
    )
    assert result.chapters[26].title == (
        "Глава XXVI. Призыв овладеть Италией и освободить ее из рук варваров"
    )

    for ch in result.chapters:
        assert "Примечания" not in ch.body
        assert "Комментарии" not in ch.body
        assert "перевод латинского выражения" not in ch.body
        assert "[1]" not in ch.body
        assert '"..' not in ch.body
        assert "\n\n" in ch.body


def test_html_and_fb2_extraction_preserves_paragraphs_and_inline_links(tmp_path):
    raw_html = (
        '<html lang="en"><body>'
        '<div class="title1"><p class="p">ГЛАВА VI</p>'
        '<p class="p">О НОВЫХ ГОСУДАРСТВАХ</p></div>'
        '<p class="p1">Первое предложение абзаца с цитатой «exemplum»* '
        '<a class="a" href="notes.xhtml#n1">[1]</a>. Второе предложение того же абзаца.</p>'
        '<p class="p1">Следующий отдельный абзац с <em>курсивом внутри</em> строки.</p>'
        "</body></html>"
    )
    extracted = _from_html(raw_html, "gosudar.html", title="Государь")
    # Cyrillic prose overrides boilerplate lang="en"
    assert extracted.language == "ru"
    paras = [p for p in extracted.text.split("\n\n") if p.strip()]
    assert len(paras) == 4
    assert "«" not in paras[2]  # folded quotes
    assert '"exemplum"* [1]. Второе предложение того же абзаца.' in paras[2]
    assert "Следующий отдельный абзац с курсивом внутри строки." == paras[3]

    fb2_path = tmp_path / "sample.fb2"
    fb2_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        "<FictionBook><description><title-info>"
        "<author><first-name>Никколо</first-name><last-name>Макиавелли</last-name></author>"
        "<book-title>Государь</book-title><lang>ru</lang>"
        "</title-info></description>"
        "<body><section><p>Основной русский текст главы с достаточным числом слов.</p></section></body>"
        '<body name="notes"><section><p>Скрытое примечание в конце файла.</p></section></body>'
        "</FictionBook>",
        encoding="utf-8",
    )
    fb2_extracted = extract_path(fb2_path)
    assert fb2_extracted.language == "ru"
    assert "Основной русский текст" in fb2_extracted.text
    assert "Скрытое примечание" not in fb2_extracted.text


def test_slugify_ascii_and_language_detection():
    assert slugify_ascii("Государь") == "gosudar"
    assert slugify_ascii("Технология жизни") == "tehnologiya-zhizni"
    assert slugify_ascii("Niccolò Machiavelli — The Prince") == "niccolo-machiavelli-the-prince"
    assert detect_language("Все государства и державы обладали властью над людьми.", "en") == "ru"
    assert detect_language("All states and powers that have held rule over men.", None) == "en"


def test_ingest_gosudar_coexists_with_english_prince_and_sets_russian_lang(data_dir, tmp_path):
    from onechapter.library import bootstrap, get_book, ingest_file

    bootstrap()
    en_prince = get_book(None, "the-prince")
    assert en_prince is not None
    assert en_prince["language"] == "en"
    assert en_prince["chapter_count"] == 27

    ru_file = tmp_path / "nikkolo_makiavelli-gosudar.txt"
    ru_file.write_text(_synthetic_gosudar_text(), encoding="utf-8")
    ru_prince = ingest_file(ru_file)

    assert ru_prince["slug"] == "gosudar"
    assert ru_prince["slug"] != en_prince["slug"]
    assert ru_prince["title"] == "Государь"
    assert ru_prince["author"] == "Никколо Макиавелли"
    assert ru_prince["language"] == "ru"
    assert ru_prince["profile"] == "prince"
    assert ru_prince["chapter_count"] == 27

    # Generic Russian upload (non-Tarasov, non-Gosudar) also gets ASCII slug and language="ru"
    generic_ru = tmp_path / "размышления.txt"
    generic_ru.write_text(
        "Размышления в тишине\n\n# Раздел первый\n\n"
        + ("Короткое русское размышление для медленного ежедневного чтения в тишине. " * 6)
        + "\n\n# Раздел второй\n\n"
        + ("Второе короткое русское размышление для следующего календарного дня чтения. " * 6),
        encoding="utf-8",
    )
    generic_book = ingest_file(generic_ru)
    assert generic_book["slug"] == "razmyshleniya-v-tishine"
    assert generic_book["language"] == "ru"

    client = Client()
    start_resp = client.post("/book/gosudar/start", follow=True)
    assert start_resp.status_code == 200
    today_html = start_resp.content.decode("utf-8")
    assert '<article class="chapter" lang="ru">' in today_html
    assert "Посвящение Лоренцо деи Медичи" in today_html

    read_resp = client.get("/read/gosudar/0")
    assert read_resp.status_code == 200
    read_html = read_resp.content.decode("utf-8")
    assert '<article class="chapter" lang="ru">' in read_html

    # Switching back to English The Prince renders lang="en"
    en_start = client.post("/book/the-prince/start", follow=True)
    assert '<article class="chapter" lang="en">' in en_start.content.decode("utf-8")
    en_read = client.get("/read/the-prince/0")
    assert '<article class="chapter" lang="en">' in en_read.content.decode("utf-8")
