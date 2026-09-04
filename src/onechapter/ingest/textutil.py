from __future__ import annotations

import re
import unicodedata

_DASHES = dict.fromkeys(map(ord, "—–−‒―"), "-")
_QUOTES = {
    ord("«"): '"',
    ord("»"): '"',
    ord("“"): '"',
    ord("”"): '"',
    ord("‘"): "'",
    ord("’"): "'",
}


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = text.translate(_DASHES).translate(_QUOTES)
    text = text.replace("ё", "е").replace("Ё", "Е")
    return text


def squeeze(text: str) -> str:
    return re.sub(r"[ \t]+", " ", fold(text)).strip()


def normalize_title(text: str) -> str:
    text = squeeze(text).lower()
    text = re.sub(r"[«»\"'“”‘’.,:;!?()\[\]{}]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def title_regex(title: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in squeeze(title).split() if p]
    if not parts:
        return re.compile(r"(?!x)")
    return re.compile(r"\s+".join(parts), re.IGNORECASE)


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text, flags=re.UNICODE))


def paragraphize(text: str) -> str:
    text = fold(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def slugify(text: str) -> str:
    text = fold(text).lower()
    text = re.sub(r"[^a-z0-9а-я]+", "-", text, flags=re.IGNORECASE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:80] or "book"
