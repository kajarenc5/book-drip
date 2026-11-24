from __future__ import annotations

from django.test import Client

from onechapter.ingest.art_of_war import CHAPTER_NUMBERS, ROMAN_NUMERALS
from onechapter.ingest.extract import ExtractedBook
from onechapter.ingest.split import split_extracted
from onechapter.ingest.textutil import slugify_ascii


_CHAPTER_SUBTITLES = {
    1: "Предварительные расчеты",
    2: "Ведение войны",
    3: "Стратегическое нападение",
    4: "Форма",
    5: "Мощь",
    6: "Полнота и пустота",
    7: "Борьба на войне",
    8: "Девять изменений",
    9: "Поход",
    10: "Формы местности",
    11: "Девять местностей",
    12: "Огневое нападение",
    13: "Использование шпионов",
}


def _synthetic_art_of_war_text(
    *,
    use_roman: bool = False,
    include_preface: bool = True,
    include_commentary: bool = True,
) -> str:
    parts: list[str] = [
        "Сунь-цзы",
        "",
        "Искусство войны (в переводе академика Н. И. Конрада)",
        "",
    ]
    if include_preface:
        parts.extend(
            [
                "Предисловие переводчика",
                "",
                ("Исторический обзор военных канонов Древнего Китая и биография Сунь-цзы. " * 10).strip(),
                "",
                "Переводчик",
                "",
            ]
        )

    for num in CHAPTER_NUMBERS:
        label = ROMAN_NUMERALS[num - 1] if use_roman else str(num)
        subtitle = _CHAPTER_SUBTITLES[num]
        if num == 1:
            heading = f"Глава {label}[*]. {subtitle} [**]"
        else:
            heading = f"Глава {label}. {subtitle}"
        editorial = (
            " [полководец, получив повеление от своего государя, формирует армию]"
            if num == 8
            else ""
        )
        parts.extend(
            [
                heading,
                "",
                (
                    f"1. Сунь-цзы сказал: синтетический первый пункт главы {num} "
                    f"о стратегии и расчете сил.[1]{editorial} " * 3
                ).strip(),
                "",
                (
                    f"2. Второй синтетический пункт главы {num} о дисциплине, "
                    f"местности и победе без сражения.[2] " * 3
                ).strip(),
                "",
            ]
        )

    if include_commentary:
        parts.extend(
            [
                (
                    'Глава 1[*]. Некоторые особенно спорные места перевода оговорены в "Примечаниях", '
                    "помещенных в конце настоящей работы."
                ),
                "",
                (
                    "Глава 1[**]. Ввиду того, что в разных изданиях трактата дается различная "
                    "разбивка на абзацы, здесь принят порядок комментированного издания."
                ),
                "",
                "1.1. В комментаторской литературе существуют большие разногласия о слове цзин.",
                "",
                'Глава 2[*]. В переводе текста II главы мною употребляется слово "князья".',
                "",
                "Так я передаю по-русски китайское обозначение чжухоу.",
            ]
        )
    return "\n".join(parts)


def test_art_of_war_profile_splits_13_chapters_and_strips_preface_notes_and_markers():
    book = ExtractedBook(
        filename="_sun-tczi-iskusstvo_vojni-6aa6eddfc3ffc.mobi",
        text=_synthetic_art_of_war_text(),
        title="Искусство войны",
        author="Сунь Цзы",
        language="ru",
    )
    result = split_extracted(book)

    assert result.profile == "art_of_war"
    assert result.slug == "iskusstvo-voyny"
    assert slugify_ascii("Искусство войны") == "iskusstvo-voyny"
    assert result.language == "ru"
    assert result.title == "Искусство войны"
    assert result.author == "Сунь-цзы"
    assert len(result.chapters) == 13
    assert result.skipped == []

    assert result.chapters[0].title == "Глава 1. Предварительные расчеты"
    assert result.chapters[12].title == "Глава 13. Использование шпионов"
    assert "[полководец, получив повеление от своего государя, формирует армию]" in result.chapters[7].body

    for idx, ch in enumerate(result.chapters, start=1):
        assert ch.part == "Искусство войны"
        assert ch.title == f"Глава {idx}. {_CHAPTER_SUBTITLES[idx]}"
        assert "[*]" not in ch.title
        assert "[**]" not in ch.title
        assert "[1]" not in ch.body
        assert "[2]" not in ch.body
        assert "Предисловие переводчика" not in ch.body
        assert "комментаторской литературе" not in ch.body
        assert "чжухоу" not in ch.body
        assert "\n\n" in ch.body


def test_art_of_war_profile_supports_roman_numerals_and_standard_back_matter():
    text = _synthetic_art_of_war_text(use_roman=True, include_commentary=False) + (
        "\n\nПримечания\n\n1. Справочное примечание редактора к изданию трактата."
    )
    book = ExtractedBook(
        filename="sun-tzu-iskusstvo-voyny.fb2",
        text=text,
        title="Искусство войны",
        author="Сунь-цзы",
        language="ru",
    )
    result = split_extracted(book)

    assert result.profile == "art_of_war"
    assert len(result.chapters) == 13
    assert result.chapters[0].title == "Глава 1. Предварительные расчеты"
    assert result.chapters[12].title == "Глава 13. Использование шпионов"
    assert "Справочное примечание" not in result.chapters[12].body


def test_ingest_art_of_war_stores_canonical_slug_and_russian_lang(data_dir, tmp_path):
    from onechapter.library import bootstrap, ingest_file

    bootstrap()
    sample = tmp_path / "_sun-tczi-iskusstvo_vojni-sample.txt"
    sample.write_text(_synthetic_art_of_war_text(), encoding="utf-8")
    ingested = ingest_file(sample)

    assert ingested["slug"] == "iskusstvo-voyny"
    assert ingested["title"] == "Искусство войны"
    assert ingested["author"] == "Сунь-цзы"
    assert ingested["language"] == "ru"
    assert ingested["profile"] == "art_of_war"
    assert ingested["chapter_count"] == 13

    client = Client()
    start_resp = client.post("/book/iskusstvo-voyny/start", follow=True)
    assert start_resp.status_code == 200
    html = start_resp.content.decode("utf-8")
    assert '<article class="chapter" lang="ru">' in html
    assert "Глава 1. Предварительные расчеты" in html
