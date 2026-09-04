from pathlib import Path

from onechapter.seed import load_split_json


def test_seeded_prince_has_dedication_and_26_chapters():
    path = Path("data/seed/the-prince.json")
    split = load_split_json(path)
    assert len(split.chapters) == 27
    assert split.chapters[0].title.startswith("Dedication")
    assert "Lorenzo" in split.chapters[0].body
    assert split.chapters[1].title.startswith("Chapter I")
    assert "republics or principalities" in split.chapters[1].body.lower()
    assert split.chapters[-1].title.startswith("Chapter XXVI")
    assert all(len(ch.body) > 80 for ch in split.chapters)
