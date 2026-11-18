from __future__ import annotations

import os
import re
import shutil
import tempfile
import warnings
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from onechapter import config
from onechapter.ingest.textutil import paragraphize, squeeze, word_count

SUPPORTED_SUFFIXES = {".mobi", ".azw", ".azw3", ".epub", ".fb2", ".html", ".htm", ".txt", ".md"}

MAX_ZIP_ENTRIES = 2_000
MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 100.0
ZIP_RATIO_MIN_UNCOMPRESSED_BYTES = 4 * 1024

MAX_MOBI_EXTRACTED_FILES = 2_000
MAX_MOBI_EXTRACTED_BYTES = 64 * 1024 * 1024

MAX_RAW_MARKUP_BYTES = 16 * 1024 * 1024
MAX_EXTRACTED_TEXT_CHARS = 8 * 1024 * 1024

MAX_METADATA_CHARS = 500
_ZIP_STREAM_CHUNK_BYTES = 64 * 1024

_NESTED_ARCHIVE_SUFFIXES = frozenset(
    {
        ".zip",
        ".epub",
        ".gz",
        ".tgz",
        ".bz2",
        ".tbz2",
        ".xz",
        ".txz",
        ".7z",
        ".rar",
        ".tar",
        ".jar",
        ".war",
        ".mobi",
        ".azw",
        ".azw3",
        ".iso",
        ".cab",
        ".zst",
        ".lzma",
    }
)

_EPUB_MARKUP_SUFFIXES = frozenset(
    {".xml", ".xhtml", ".html", ".htm", ".opf", ".ncx", ".svg"}
)

_DISGUISED_BINARY_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x7fELF", "executable binary"),
    (b"%PDF-", "PDF document"),
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF87a", "GIF image"),
    (b"GIF89a", "GIF image"),
    (b"Rar!\x1a\x07", "RAR archive"),
    (b"7z\xbc\xaf\x27\x1c", "7z archive"),
    (b"\x1f\x8b", "gzip archive"),
    (b"BZh", "bzip2 archive"),
    (b"\xfd7zXZ\x00", "xz archive"),
    (b"SQLite format 3\x00", "SQLite database"),
)

_NESTED_ARCHIVE_SIGNATURES: tuple[bytes, ...] = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x1f\x8b",
    b"BZh",
    b"Rar!\x1a\x07",
    b"7z\xbc\xaf\x27\x1c",
    b"\xfd7zXZ\x00",
)

_ENTITY_DECL_RE = re.compile(r"<!\s*ENTITY\b", re.IGNORECASE)
_XML_SYSTEM_DOCTYPE_RE = re.compile(r"<!\s*DOCTYPE\b[^>]*\bSYSTEM\b", re.IGNORECASE)
_HTML_SYSTEM_DOCTYPE_RE = re.compile(
    r"<!\s*DOCTYPE\b[^>]*\bSYSTEM\s+['\"](?!about:legacy-compat['\"])[^'\"]+['\"]",
    re.IGNORECASE,
)
_UNSAFE_HTML_DOCTYPE_URI_RE = re.compile(
    r"<!\s*DOCTYPE\b[^>]*\b(?:SYSTEM|PUBLIC)\b[^>]*['\"]\s*(?:file|ftp|gopher|jar|netdoc|php|data):",
    re.IGNORECASE,
)
_XINCLUDE_RE = re.compile(
    r"(?:http://www\.w3\.org/2001/XInclude|<\s*(?:[\w.-]+:)?include\b[^>]*\bhref\s*=)",
    re.IGNORECASE,
)
_XML_ENCODING_RE = re.compile(br"<\?xml[^>]*?\bencoding\s*=\s*['\"]([A-Za-z0-9._-]+)['\"]", re.I)


class UploadValidationError(ValueError):
    """Domain validation failure for untrusted book uploads."""

    def __init__(self, message: str, *, code: str = "fail") -> None:
        super().__init__(message)
        self.code = code
        self.user_message = message


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


def _clamp_meta(value: str | None, limit: int = MAX_METADATA_CHARS) -> str | None:
    if value is None:
        return None
    cleaned = squeeze(value)
    if not cleaned:
        return None
    return cleaned[:limit]


def _safe_xml_parser_factory(**kwargs):
    from lxml import etree

    kwargs["resolve_entities"] = False
    kwargs["no_network"] = True
    kwargs["load_dtd"] = False
    kwargs["dtd_validation"] = False
    kwargs["huge_tree"] = False
    kwargs["collect_ids"] = False
    return etree.XMLParser(**kwargs)


def _safe_html_parser_factory(**kwargs):
    from lxml import etree

    kwargs["no_network"] = True
    kwargs["huge_tree"] = False
    return etree.HTMLParser(**kwargs)


def safe_html_soup(html: str):
    """Parse untrusted HTML using lxml with network access and huge_tree disabled."""
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
    from bs4.builder._lxml import LXMLTreeBuilder
    from bs4.exceptions import ParserRejectedMarkup

    raw_len = len(html.encode("utf-8", errors="ignore"))
    if raw_len > MAX_RAW_MARKUP_BYTES:
        raise UploadValidationError(
            "The HTML content inside that book exceeds the safe size limit.",
            code="too_large",
        )
    _assert_safe_markup_text(html, is_xml=False)

    class _SafeHTMLTreeBuilder(LXMLTreeBuilder):
        def default_parser(self, encoding=None):
            return _safe_html_parser_factory

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    try:
        return BeautifulSoup(
            html,
            features=None,
            builder=_SafeHTMLTreeBuilder(huge_tree=False),
        )
    except (ParserRejectedMarkup, ValueError, MemoryError) as exc:
        raise UploadValidationError(
            "That HTML file could not be parsed safely.",
            code="corrupt",
        ) from exc


def safe_xml_soup(xml: str | bytes):
    """Parse untrusted XML using lxml with entities, DTD loading, network, and huge_tree disabled."""
    from bs4 import BeautifulSoup
    from bs4.builder._lxml import LXMLTreeBuilderForXML
    from bs4.exceptions import ParserRejectedMarkup

    if isinstance(xml, bytes):
        if len(xml) > MAX_RAW_MARKUP_BYTES:
            raise UploadValidationError(
                "The XML content inside that book exceeds the safe size limit.",
                code="too_large",
            )
        _assert_safe_xml_bytes(xml)
    else:
        if len(xml.encode("utf-8", errors="ignore")) > MAX_RAW_MARKUP_BYTES:
            raise UploadValidationError(
                "The XML content inside that book exceeds the safe size limit.",
                code="too_large",
            )
        _assert_safe_markup_text(xml, is_xml=True)

    builder = LXMLTreeBuilderForXML(parser=_safe_xml_parser_factory, huge_tree=False)
    try:
        return BeautifulSoup(xml, features=None, builder=builder)
    except (ParserRejectedMarkup, ValueError, MemoryError) as exc:
        raise UploadValidationError(
            "That XML file could not be parsed safely.",
            code="corrupt",
        ) from exc


def _assert_safe_markup_text(text: str, *, is_xml: bool) -> None:
    if _ENTITY_DECL_RE.search(text):
        raise UploadValidationError(
            "XML entity declarations are not allowed in uploaded books.",
            code="unsafe",
        )
    if _XINCLUDE_RE.search(text):
        raise UploadValidationError(
            "External XML includes are not allowed in uploaded books.",
            code="unsafe",
        )
    if is_xml and _XML_SYSTEM_DOCTYPE_RE.search(text):
        raise UploadValidationError(
            "External XML DTD references are not allowed in uploaded books.",
            code="unsafe",
        )
    if not is_xml and _HTML_SYSTEM_DOCTYPE_RE.search(text):
        raise UploadValidationError(
            "External HTML DTD references are not allowed in uploaded books.",
            code="unsafe",
        )
    if _UNSAFE_HTML_DOCTYPE_URI_RE.search(text):
        raise UploadValidationError(
            "External DTD references are not allowed in uploaded books.",
            code="unsafe",
        )


def _decode_markup_probe(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return raw.decode("utf-32", errors="ignore")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="ignore")
    return raw.decode("latin-1", errors="ignore")


def _assert_safe_xml_bytes(raw: bytes, *, strict_system_doctype: bool = True) -> None:
    probe = _decode_markup_probe(raw)
    _assert_safe_markup_text(probe, is_xml=strict_system_doctype)


def validate_source_file(path: Path) -> str:
    """Validate existence, file size, extension, and file header signatures before parsing."""
    if not path.exists() or path.is_symlink() or not path.is_file():
        raise UploadValidationError("Uploaded file was not found.", code="fail")

    size = path.stat().st_size
    if size <= 0:
        raise UploadValidationError("The uploaded file is empty.", code="empty")
    if size > config.max_upload_bytes():
        raise UploadValidationError(
            "That file exceeds the maximum allowed upload size.",
            code="too_large",
        )

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UploadValidationError(
            f"Unsupported file format '{suffix or path.name}'. Upload .mobi, .epub, .fb2, .html, or .txt.",
            code="unsupported",
        )

    with path.open("rb") as handle:
        head = handle.read(4096)

    for magic, label in _DISGUISED_BINARY_SIGNATURES:
        if head.startswith(magic):
            raise UploadValidationError(
                f"Disguised {label} files cannot be uploaded as books.",
                code="unsupported",
            )

    if suffix == ".epub":
        if not head.startswith(b"PK\x03\x04"):
            raise UploadValidationError(
                "That file is not a valid EPUB archive.",
                code="corrupt",
            )
    elif suffix in {".mobi", ".azw", ".azw3"}:
        if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            raise UploadValidationError(
                "That file is a ZIP archive rather than a Kindle (.mobi/.azw) book.",
                code="corrupt",
            )
    else:
        if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"MZ")):
            raise UploadValidationError(
                "Binary or archive files cannot be uploaded with a text or markup extension.",
                code="unsupported",
            )

    return suffix


def _decode_text_file(path: Path, *, max_bytes: int | None = None) -> str:
    raw = path.read_bytes()
    if max_bytes is not None and len(raw) > max_bytes:
        raise UploadValidationError(
            "That text or markup file exceeds the safe in-memory parsing size limit.",
            code="too_large",
        )
    if not raw:
        raise UploadValidationError("The uploaded file is empty.", code="empty")

    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        raise UploadValidationError("UTF-32 encoded files are not supported.", code="unsupported")

    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise UploadValidationError(
                "That file contains invalid UTF-16 text encoding.",
                code="corrupt",
            ) from exc
    else:
        if b"\x00" in raw:
            raise UploadValidationError(
                "Binary files cannot be uploaded as plain text or markup.",
                code="corrupt",
            )
        m = _XML_ENCODING_RE.search(raw[:256])
        declared_enc = m.group(1).decode("ascii", errors="ignore") if m else None
        text = None
        for enc in ([declared_enc] if declared_enc else []) + ["utf-8-sig", "cp1251"]:
            try:
                text = raw.decode(enc)
                break
            except (LookupError, UnicodeDecodeError):
                continue
        if text is None:
            text = raw.decode("utf-8", errors="replace")

    sample = text[:8192]
    if sample:
        bad_ctrl = sum(
            1
            for ch in sample
            if ord(ch) < 32 and ch not in {"\n", "\r", "\t", "\f"}
        )
        if bad_ctrl / len(sample) > 0.02:
            raise UploadValidationError(
                "That file appears to contain binary data rather than readable book text.",
                code="corrupt",
            )

    if len(text) > MAX_EXTRACTED_TEXT_CHARS and path.suffix.lower() in {".txt", ".md"}:
        raise UploadValidationError(
            "That text file exceeds the maximum supported book length.",
            code="too_large",
        )
    return text


def extract_path(path: Path) -> ExtractedBook:
    path = Path(path)
    suffix = validate_source_file(path)
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
    raise UploadValidationError(f"Unsupported file type: {suffix or path.name}", code="unsupported")


def _from_text(path: Path) -> ExtractedBook:
    text = _decode_text_file(path)
    cleaned = paragraphize(text)
    if len(cleaned) > MAX_EXTRACTED_TEXT_CHARS:
        raise UploadValidationError(
            "Extracted book text exceeds the maximum supported length.",
            code="too_large",
        )
    return ExtractedBook(
        filename=path.name,
        text=cleaned,
        title=_clamp_meta(_guess_title(path, cleaned)),
    )


def _from_html_file(path: Path) -> ExtractedBook:
    html = _decode_text_file(path, max_bytes=MAX_RAW_MARKUP_BYTES)
    return _from_html(html, path.name, _guess_title(path, ""))


def _from_html(html: str, filename: str, fallback_title: str | None = None) -> ExtractedBook:
    soup = safe_html_soup(html)
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()
    title = None
    if soup.title and soup.title.string:
        title = squeeze(soup.title.string)
    text = paragraphize(soup.get_text("\n"))
    if len(text) > MAX_EXTRACTED_TEXT_CHARS:
        raise UploadValidationError(
            "Extracted HTML text exceeds the maximum supported book length.",
            code="too_large",
        )
    if not title:
        title = _guess_title(Path(filename), text) if text else fallback_title
    return ExtractedBook(
        filename=filename,
        html=html,
        text=text,
        title=_clamp_meta(title),
        author=_clamp_meta(_meta_author(soup)),
    )


def _validate_zip_entry_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not name or "\x00" in name:
        raise UploadValidationError(
            "That EPUB archive contains an invalid entry filename.",
            code="unsafe",
        )
    if name.startswith(("/", "\\")) or re.match(r"^[a-zA-Z]:", name):
        raise UploadValidationError(
            "That EPUB archive contains an unsafe absolute file path.",
            code="unsafe",
        )
    segments = [seg for seg in re.split(r"[/\\]+", name) if seg]
    if any(seg == ".." for seg in segments):
        raise UploadValidationError(
            "That EPUB archive contains an unsafe path traversal entry.",
            code="unsafe",
        )
    unix_mode = (info.external_attr >> 16) & 0o170000
    if unix_mode == 0o120000:
        raise UploadValidationError(
            "Symbolic links inside EPUB archives are not allowed.",
            code="unsafe",
        )
    normalized = "/".join(segments)
    if not info.is_dir():
        for ext in PurePosixPath(normalized).suffixes:
            if ext.lower() in _NESTED_ARCHIVE_SUFFIXES:
                raise UploadValidationError(
                    "Nested archive files inside EPUB uploads are not allowed.",
                    code="unsafe",
                )
    return normalized


def _check_epub_encryption_manifest(raw: bytes) -> None:
    """Reject DRM-encrypted EPUB content while permitting standard IDPF/Adobe font obfuscation."""
    _assert_safe_xml_bytes(raw)
    text = raw.decode("utf-8", errors="ignore")
    if "EncryptedData" not in text:
        return
    lower = text.lower()
    if any(
        ext in lower
        for ext in (".xhtml", ".html", ".htm", ".xml", ".opf", "adept", "fairplay")
    ):
        raise UploadValidationError(
            "DRM-protected or encrypted EPUB books cannot be imported.",
            code="corrupt",
        )


def _inspect_epub_zip(path: Path) -> None:
    """Validate EPUB ZIP headers and stream-verify decompression safety before ebooklib runs."""
    try:
        zf_ctx = zipfile.ZipFile(path, "r")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, zlib.error, ValueError) as exc:
        raise UploadValidationError(
            "That EPUB archive is corrupt or unreadable.",
            code="corrupt",
        ) from exc

    with zf_ctx as zf:
        infos = zf.infolist()
        if not infos:
            raise UploadValidationError("That EPUB archive is empty.", code="corrupt")
        if len(infos) > MAX_ZIP_ENTRIES:
            raise UploadValidationError(
                f"That EPUB archive has too many files ({len(infos)} entries).",
                code="unsafe",
            )

        declared_total = 0
        declared_compressed = 0
        has_container_xml = False

        for info in infos:
            normalized = _validate_zip_entry_name(info)
            if info.flag_bits & 0x1:
                raise UploadValidationError(
                    "Password-protected or encrypted EPUB archives are not supported.",
                    code="corrupt",
                )
            if info.file_size < 0 or info.compress_size < 0:
                raise UploadValidationError(
                    "That EPUB archive has invalid entry size headers.",
                    code="corrupt",
                )
            if info.file_size > MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES:
                raise UploadValidationError(
                    "An entry inside that EPUB exceeds the maximum uncompressed size limit.",
                    code="unsafe",
                )
            declared_total += info.file_size
            declared_compressed += info.compress_size
            if declared_total > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
                raise UploadValidationError(
                    "That EPUB expands beyond the maximum allowed uncompressed size.",
                    code="unsafe",
                )
            if info.file_size >= ZIP_RATIO_MIN_UNCOMPRESSED_BYTES:
                if (
                    info.compress_size <= 0
                    or (info.file_size / info.compress_size) > MAX_ZIP_COMPRESSION_RATIO
                ):
                    raise UploadValidationError(
                        "That EPUB archive exceeds the maximum allowed compression ratio.",
                        code="unsafe",
                    )
            if normalized == "META-INF/container.xml" and not info.is_dir():
                has_container_xml = True

        if declared_total >= ZIP_RATIO_MIN_UNCOMPRESSED_BYTES:
            if (
                declared_compressed <= 0
                or (declared_total / declared_compressed) > MAX_ZIP_COMPRESSION_RATIO
            ):
                raise UploadValidationError(
                    "That EPUB archive exceeds the maximum allowed overall compression ratio.",
                    code="unsafe",
                )

        if not has_container_xml:
            raise UploadValidationError(
                "That file is missing META-INF/container.xml and is not a valid EPUB.",
                code="corrupt",
            )

        actual_total = 0
        for info in infos:
            if info.is_dir():
                continue
            normalized = "/".join(seg for seg in re.split(r"[/\\]+", info.filename) if seg)
            ext = PurePosixPath(normalized).suffix.lower()
            collect_markup = ext in _EPUB_MARKUP_SUFFIXES or normalized == "META-INF/encryption.xml"
            markup_buf = bytearray() if collect_markup else None
            entry_actual = 0
            checked_magic = False

            try:
                with zf.open(info, "r") as stream:
                    while True:
                        chunk = stream.read(_ZIP_STREAM_CHUNK_BYTES)
                        if not chunk:
                            break
                        if not checked_magic:
                            checked_magic = True
                            if any(chunk.startswith(sig) for sig in _NESTED_ARCHIVE_SIGNATURES):
                                raise UploadValidationError(
                                    "Nested compressed archives inside EPUB files are not allowed.",
                                    code="unsafe",
                                )
                        entry_actual += len(chunk)
                        actual_total += len(chunk)
                        if entry_actual > MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES:
                            raise UploadValidationError(
                                "An entry inside that EPUB exceeds the maximum uncompressed size limit.",
                                code="unsafe",
                            )
                        if actual_total > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
                            raise UploadValidationError(
                                "That EPUB expands beyond the maximum allowed uncompressed size.",
                                code="unsafe",
                            )
                        if entry_actual >= ZIP_RATIO_MIN_UNCOMPRESSED_BYTES:
                            if (
                                info.compress_size <= 0
                                or (entry_actual / info.compress_size) > MAX_ZIP_COMPRESSION_RATIO
                            ):
                                raise UploadValidationError(
                                    "That EPUB archive exceeds the maximum allowed compression ratio.",
                                    code="unsafe",
                                )
                        if markup_buf is not None:
                            if len(markup_buf) + len(chunk) > MAX_RAW_MARKUP_BYTES:
                                raise UploadValidationError(
                                    "Markup inside that EPUB exceeds the safe parsing limit.",
                                    code="too_large",
                                )
                            markup_buf.extend(chunk)
            except UploadValidationError:
                raise
            except (
                zipfile.BadZipFile,
                OSError,
                EOFError,
                zlib.error,
                RuntimeError,
                NotImplementedError,
            ) as exc:
                raise UploadValidationError(
                    "That EPUB archive contains corrupt or unsupported compressed data.",
                    code="corrupt",
                ) from exc

            if markup_buf is not None:
                if normalized == "META-INF/encryption.xml":
                    _check_epub_encryption_manifest(bytes(markup_buf))
                else:
                    strict_xml = ext in {".xml", ".opf", ".ncx", ".svg"}
                    _assert_safe_xml_bytes(
                        bytes(markup_buf),
                        strict_system_doctype=strict_xml,
                    )

        if actual_total >= ZIP_RATIO_MIN_UNCOMPRESSED_BYTES:
            if (
                declared_compressed <= 0
                or (actual_total / declared_compressed) > MAX_ZIP_COMPRESSION_RATIO
            ):
                raise UploadValidationError(
                    "That EPUB archive exceeds the maximum allowed overall compression ratio.",
                    code="unsafe",
                )


def _from_epub(path: Path, original_name: str | None = None) -> ExtractedBook:
    import ebooklib
    from ebooklib import epub

    _inspect_epub_zip(path)

    try:
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
    except UploadValidationError:
        raise
    except Exception as exc:
        raise UploadValidationError(
            "That EPUB file is malformed or could not be opened.",
            code="corrupt",
        ) from exc

    title = None
    author = None
    try:
        if book.get_metadata("DC", "title"):
            title = str(book.get_metadata("DC", "title")[0][0])
        if book.get_metadata("DC", "creator"):
            author = str(book.get_metadata("DC", "creator")[0][0])
    except Exception:
        pass

    chunks: list[str] = []
    html_chunks: list[str] = []
    total_html_bytes = 0
    total_text_chars = 0

    try:
        doc_items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
    except Exception as exc:
        raise UploadValidationError(
            "That EPUB file has an unreadable document spine.",
            code="corrupt",
        ) from exc

    for item in doc_items:
        try:
            raw_bytes = item.get_content()
        except Exception as exc:
            raise UploadValidationError(
                "A chapter inside that EPUB could not be read.",
                code="corrupt",
            ) from exc
        total_html_bytes += len(raw_bytes)
        if total_html_bytes > MAX_RAW_MARKUP_BYTES:
            raise UploadValidationError(
                "Combined chapter markup inside that EPUB exceeds the safe parsing limit.",
                code="too_large",
            )
        raw = raw_bytes.decode("utf-8", errors="ignore")
        soup = safe_html_soup(raw)
        for tag in soup(["script", "style"]):
            tag.decompose()
        item_text = soup.get_text("\n")
        total_text_chars += len(item_text)
        if total_text_chars > MAX_EXTRACTED_TEXT_CHARS:
            raise UploadValidationError(
                "Extracted text inside that EPUB exceeds the maximum supported book length.",
                code="too_large",
            )
        html_chunks.append(raw)
        chunks.append(item_text)

    combined_text = paragraphize("\n\n".join(chunks))
    if len(combined_text) > MAX_EXTRACTED_TEXT_CHARS:
        raise UploadValidationError(
            "Extracted text inside that EPUB exceeds the maximum supported book length.",
            code="too_large",
        )
    html = "\n".join(html_chunks)
    resolved_title = title or _guess_title(Path(original_name or path.name), combined_text)
    return ExtractedBook(
        filename=original_name or path.name,
        text=combined_text,
        html=html,
        title=_clamp_meta(resolved_title),
        author=_clamp_meta(author),
    )


def _from_fb2(path: Path) -> ExtractedBook:
    from lxml import etree

    raw = path.read_bytes()
    if b"\x00" in raw and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UploadValidationError(
            "That FB2 file contains invalid binary data.",
            code="corrupt",
        )
    if len(raw) > MAX_RAW_MARKUP_BYTES and b"<binary" not in raw:
        raise UploadValidationError(
            "That FB2 XML document exceeds the safe parsing size limit.",
            code="too_large",
        )
    _assert_safe_xml_bytes(raw, strict_system_doctype=True)

    strict_parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        recover=False,
        huge_tree=False,
        collect_ids=False,
    )
    try:
        root = etree.fromstring(raw, parser=strict_parser)
    except (etree.XMLSyntaxError, ValueError, MemoryError) as exc:
        raise UploadValidationError(
            "That FB2 file contains malformed or unsafe XML.",
            code="corrupt",
        ) from exc

    docinfo = root.getroottree().docinfo
    if docinfo.internalDTD is not None and list(docinfo.internalDTD.entities()):
        raise UploadValidationError(
            "XML entity declarations are not allowed in FB2 uploads.",
            code="unsafe",
        )
    if docinfo.system_url:
        raise UploadValidationError(
            "External DTD references are not allowed in FB2 uploads.",
            code="unsafe",
        )

    try:
        local_root = etree.QName(root.tag).localname.lower()
    except ValueError as exc:
        raise UploadValidationError(
            "That file is not a valid FictionBook (.fb2) document.",
            code="corrupt",
        ) from exc
    if local_root != "fictionbook":
        raise UploadValidationError(
            "That file is not a valid FictionBook (.fb2) document.",
            code="corrupt",
        )

    for binary_el in list(root.iter()):
        if isinstance(binary_el.tag, str) and binary_el.tag.rsplit("}", 1)[-1].lower() == "binary":
            parent = binary_el.getparent()
            if parent is not None:
                parent.remove(binary_el)

    cleaned_xml = etree.tostring(root, encoding="unicode")
    if len(cleaned_xml.encode("utf-8", errors="ignore")) > MAX_RAW_MARKUP_BYTES:
        raise UploadValidationError(
            "That FB2 document exceeds the safe parsing size limit.",
            code="too_large",
        )

    soup = safe_xml_soup(cleaned_xml)
    title = None
    author = None
    title_el = soup.find("book-title")
    if title_el:
        title = squeeze(title_el.get_text(" "))
    author_scope = soup.find("title-info") or soup
    first = author_scope.find("first-name")
    last = author_scope.find("last-name")
    names = [squeeze(n.get_text()) for n in (first, last) if n and squeeze(n.get_text())]
    if names:
        author = " ".join(names)

    bodies: list[str] = []
    total_chars = 0
    for body in soup.find_all("body"):
        chunk = body.get_text("\n")
        total_chars += len(chunk)
        if total_chars > MAX_EXTRACTED_TEXT_CHARS:
            raise UploadValidationError(
                "Extracted text inside that FB2 exceeds the maximum supported book length.",
                code="too_large",
            )
        if chunk.strip():
            bodies.append(chunk)

    text = paragraphize("\n\n".join(bodies))
    return ExtractedBook(
        filename=path.name,
        text=text,
        html=cleaned_xml,
        title=_clamp_meta(title or (_guess_title(path, text) if text else None)),
        author=_clamp_meta(author),
    )


def _enforce_extracted_dir_limits(extracted_dir: Path) -> None:
    """Enforce file-count, symlink, path-containment, and total byte caps on an unpacked temp dir."""
    root = extracted_dir.resolve()
    file_count = 0
    total_bytes = 0

    for current_root, dirnames, filenames in os.walk(root, followlinks=False):
        cur = Path(current_root)
        for dname in dirnames:
            dpath = cur / dname
            if dpath.is_symlink():
                raise UploadValidationError(
                    "Unpacked Kindle archive contains an unsafe symbolic link.",
                    code="unsafe",
                )
            if not dpath.resolve().is_relative_to(root):
                raise UploadValidationError(
                    "Unpacked Kindle archive escaped its temporary directory.",
                    code="unsafe",
                )
        for fname in filenames:
            fpath = cur / fname
            if fpath.is_symlink():
                raise UploadValidationError(
                    "Unpacked Kindle archive contains an unsafe symbolic link.",
                    code="unsafe",
                )
            resolved = fpath.resolve()
            if not resolved.is_relative_to(root):
                raise UploadValidationError(
                    "Unpacked Kindle archive escaped its temporary directory.",
                    code="unsafe",
                )
            file_count += 1
            if file_count > MAX_MOBI_EXTRACTED_FILES:
                raise UploadValidationError(
                    "Unpacked Kindle archive contains too many files.",
                    code="unsafe",
                )
            total_bytes += resolved.stat().st_size
            if total_bytes > MAX_MOBI_EXTRACTED_BYTES:
                raise UploadValidationError(
                    "Unpacked Kindle archive exceeds the maximum allowed extracted size.",
                    code="unsafe",
                )


def _verify_mobi_header(path: Path) -> None:
    with path.open("rb") as handle:
        header = handle.read(68)
    if len(header) < 68 or header[60:68] not in {b"BOOKMOBI", b"TEXtREAd"}:
        raise UploadValidationError(
            "That file is not a valid Kindle (.mobi/.azw) book.",
            code="corrupt",
        )


def _from_mobi(path: Path) -> ExtractedBook:
    try:
        import mobi
        from mobi.extract import extract as orig_mobi_extract
        from mobi.kindleunpack import unpackBook
    except ImportError as exc:
        raise RuntimeError("The mobi package is required to read Kindle files.") from exc

    is_patched_extract = getattr(mobi.extract, "__code__", None) is not getattr(
        orig_mobi_extract, "__code__", None
    )

    extracted_dir_path: Path | None = None
    try:
        if is_patched_extract:
            try:
                raw_dir, raw_file = mobi.extract(str(path))
            except UploadValidationError:
                raise
            except Exception as exc:
                raise UploadValidationError(
                    "That Kindle file could not be unpacked (it may be DRM-encrypted or corrupt).",
                    code="corrupt",
                ) from exc
            extracted_dir_path = Path(raw_dir)
            extracted = Path(raw_file)
        else:
            _verify_mobi_header(path)
            config.ensure_dirs()
            extracted_dir_path = Path(
                tempfile.mkdtemp(prefix="mobiex-", dir=config.DATA_DIR / "tmp")
            )
            try:
                unpackBook(str(path), str(extracted_dir_path), epubver="A")
            except UploadValidationError:
                raise
            except Exception as exc:
                msg = str(exc).lower()
                if "encrypt" in msg:
                    raise UploadValidationError(
                        "DRM-protected or encrypted Kindle files cannot be imported.",
                        code="corrupt",
                    ) from exc
                raise UploadValidationError(
                    "That Kindle file is corrupt or could not be unpacked.",
                    code="corrupt",
                ) from exc

            stem = path.stem
            candidates = (
                extracted_dir_path / "mobi8" / f"{stem}.epub",
                extracted_dir_path / "mobi7" / "book.html",
                extracted_dir_path / f"{stem}.001.pdf",
            )
            extracted = next((c for c in candidates if c.exists()), Path())
            if not extracted.name or not extracted.exists():
                raise UploadValidationError(
                    "No readable text layer was found in that Kindle file.",
                    code="corrupt",
                )

        _enforce_extracted_dir_limits(extracted_dir_path)

        resolved_root = extracted_dir_path.resolve()
        resolved_target = extracted.resolve()
        if not resolved_target.is_relative_to(resolved_root) or not resolved_target.is_file():
            raise UploadValidationError(
                "Unpacked Kindle output path is invalid.",
                code="unsafe",
            )

        target_suffix = resolved_target.suffix.lower()
        if target_suffix == ".pdf":
            raise UploadValidationError(
                "Print Replica (PDF) Kindle files are not supported.",
                code="unsupported",
            )
        if target_suffix == ".epub":
            book = _from_epub(resolved_target, original_name=path.name)
        else:
            html = _decode_text_file(resolved_target, max_bytes=MAX_RAW_MARKUP_BYTES)
            book = _from_html(html, path.name)
        if not book.title:
            book.title = _clamp_meta(_guess_title(path, book.text))
        book.filename = path.name
        book.notes.append(f"Unpacked Kindle file via {resolved_target.name}")
        return book
    finally:
        if extracted_dir_path is not None:
            shutil.rmtree(extracted_dir_path, ignore_errors=True)


def _guess_title(path: Path, text: str) -> str:
    stem = re.sub(r"[_-]+", " ", path.stem).strip()
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if first and len(first) < 80:
        return first
    return stem.title() or "Book"


def _meta_author(soup) -> str | None:
    tag = soup.find("meta", attrs={"name": re.compile("author", re.I)})
    if tag and tag.get("content"):
        return squeeze(str(tag["content"]))
    return None
