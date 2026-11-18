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
    return re.compile(
        r"^[ \t]*" + r"\s+".join(parts) + r"[ \t]*$",
        re.IGNORECASE | re.MULTILINE,
    )


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text, flags=re.UNICODE))


def paragraphize(text: str) -> str:
    text = fold(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_CYRILLIC_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    "і": "i",
    "ї": "yi",
    "є": "ye",
    "ґ": "g",
    "ў": "u",
}
_LANG_ALIASES = {
    "rus": "ru",
    "russian": "ru",
    "eng": "en",
    "english": "en",
    "ita": "it",
    "fra": "fr",
    "fre": "fr",
    "deu": "de",
    "ger": "de",
    "spa": "es",
    "ukr": "uk",
    "bel": "be",
    "hye": "hy",
    "arm": "hy",
}
_CYRILLIC_LANGS = {"ru", "uk", "be", "bg", "sr", "mk", "kk", "ky", "mn", "tg"}


def slugify(text: str) -> str:
    text = fold(text).lower()
    text = re.sub(r"[^a-z0-9а-я]+", "-", text, flags=re.IGNORECASE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:80] or "book"


def slugify_ascii(text: str) -> str:
    lowered = fold(text).lower()
    transliterated = "".join(_CYRILLIC_TRANSLIT.get(ch, ch) for ch in lowered)
    decomposed = unicodedata.normalize("NFKD", transliterated)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:80] or "book"


def normalize_lang_code(code: str | None) -> str | None:
    if not code:
        return None
    primary = re.split(r"[-_]", code.strip().lower(), maxsplit=1)[0]
    if not primary:
        return None
    if primary in _LANG_ALIASES:
        return _LANG_ALIASES[primary]
    if re.fullmatch(r"[a-z]{2,3}", primary):
        return primary
    return None


def detect_language(text: str, declared: str | None = None) -> str | None:
    norm = normalize_lang_code(declared)
    sample = text[:25000]
    cyr = len(re.findall(r"[а-яёА-ЯЁ]", sample))
    lat = len(re.findall(r"[a-zA-Z]", sample))
    total = cyr + lat
    if total >= 20:
        if cyr / total >= 0.35:
            return norm if norm in _CYRILLIC_LANGS else "ru"
        if norm and norm not in _CYRILLIC_LANGS:
            return norm
        if lat / total >= 0.6:
            return "en"
    if cyr > lat:
        return norm if norm in _CYRILLIC_LANGS else "ru"
    if norm:
        return norm
    if lat > 0:
        return "en"
    return None
