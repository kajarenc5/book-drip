from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from onechapter.ingest.textutil import paragraphize, squeeze, word_count


@dataclass
class ExtractedBook:
    filename: str
    text: str
    html: str | None = None
    title: str | None = None
    author: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return word_count(self.text)


SUPPORTED_SUFFIXES = {".mobi", ".azw", ".azw3", ".epub", ".fb2", ".html", ".htm", ".txt", ".md"}


def extract_path(path: Path) -> ExtractedBook:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return _from_text(path)
    if suffix in {".html", ".htm"}:
        return _from_html_file(path)
    if suffix == ".epub":
        return _from_epub(path)
    if suffix == ".fb2":
        return _from_fb2(path)
    if suffix in {".mobi", ".azw", ".azw3"}:
        return _from_mobi(path)
    raise ValueError(f"Unsupported file type: {suffix or path.name}")


def _from_text(path: Path) -> ExtractedBook:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return ExtractedBook(filename=path.name, text=paragraphize(text), title=_guess_title(path, text))


def _from_html_file(path: Path) -> ExtractedBook:
    html = path.read_text(encoding="utf-8", errors="ignore")
    return _from_html(html, path.name, _guess_title(path, html))


def _from_html(html: str, filename: str, title: str | None = None) -> ExtractedBook:
    import warnings
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()
    if not title:
        if soup.title and soup.title.string:
            title = squeeze(soup.title.string)
    text = soup.get_text("\n")
    return ExtractedBook(
        filename=filename,
        html=html,
        text=paragraphize(text),
        title=title,
        author=_meta_author(soup),
    )


def _from_epub(path: Path, original_name: str | None = None) -> ExtractedBook:
    import warnings
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    book = epub.read_epub(str(path), options={"ignore_ncx": True})
    title = None
    author = None
    if book.get_metadata("DC", "title"):
        title = str(book.get_metadata("DC", "title")[0][0])
    if book.get_metadata("DC", "creator"):
        author = str(book.get_metadata("DC", "creator")[0][0])

    chunks: list[str] = []
    html_chunks: list[str] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        raw = item.get_content().decode("utf-8", errors="ignore")
        html_chunks.append(raw)
        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        chunks.append(soup.get_text("\n"))

    html = "\n".join(html_chunks)
    return ExtractedBook(
        filename=original_name or path.name,
        text=paragraphize("\n\n".join(chunks)),
        html=html,
        title=title or _guess_title(path, "\n".join(chunks)),
        author=author,
    )


def _from_fb2(path: Path) -> ExtractedBook:
    from bs4 import BeautifulSoup

    xml = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(xml, "lxml-xml")
    title = None
    author = None
    title_el = soup.find("book-title")
    if title_el:
        title = squeeze(title_el.get_text(" "))
    first = soup.find("first-name")
    last = soup.find("last-name")
    names = [squeeze(n.get_text()) for n in (first, last) if n]
    if names:
        author = " ".join(names)
    bodies = []
    for body in soup.find_all("body"):
        bodies.append(body.get_text("\n"))
    return ExtractedBook(
        filename=path.name,
        text=paragraphize("\n\n".join(bodies) or soup.get_text("\n")),
        html=xml,
        title=title,
        author=author,
    )


def _from_mobi(path: Path) -> ExtractedBook:
    import shutil
    from pathlib import Path as P

    try:
        import mobi
    except ImportError as exc:
        raise RuntimeError("The mobi package is required to read Kindle files.") from exc

    extracted_dir, extracted_file = mobi.extract(str(path))
    extracted = P(extracted_file)
    try:
        if extracted.suffix.lower() == ".epub":
            book = _from_epub(extracted, original_name=path.name)
        else:
            html = extracted.read_text(encoding="utf-8", errors="ignore")
            book = _from_html(html, path.name)
        if not book.title:
            book.title = _guess_title(path, book.text)
        book.filename = path.name
        book.notes.append(f"Unpacked Kindle file via {extracted.name}")
        return book
    finally:
        shutil.rmtree(extracted_dir, ignore_errors=True)


def _guess_title(path: Path, text: str) -> str:
    stem = re.sub(r"[_-]+", " ", path.stem).strip()
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if first and len(first) < 80:
        return first
    return stem.title()


def _meta_author(soup) -> str | None:
    tag = soup.find("meta", attrs={"name": re.compile("author", re.I)})
    if tag and tag.get("content"):
        return squeeze(tag["content"])
    return None
