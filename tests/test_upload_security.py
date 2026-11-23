from __future__ import annotations

import asyncio
import io
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async as asgiref_sync_to_async
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from onechapter import config
from onechapter.auth import create_magic_login_token
from onechapter.bot.handlers import on_document
from onechapter.db import init_db
from onechapter.ingest import extract as extract_mod
from onechapter.ingest.extract import UploadValidationError, extract_path
from onechapter.library import (
    bootstrap,
    get_or_create_telegram_reader,
    ingest_file,
)
from onechapter.reader.models import Book, Chapter


def _prose(label: str, repeats: int = 8) -> str:
    return (f"{label} teaches patience and careful daily attention to one page. ") * repeats


def _valid_epub_bytes(*, extra_entries: dict[str, bytes] | None = None, chapter_xhtml: str | None = None) -> bytes:
    container_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        "<rootfiles>"
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles>"
        "</container>"
    ).encode("utf-8")

    opf_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Quiet Meditations</dc:title>"
        "<dc:creator>Marcus Test</dc:creator>"
        '<dc:identifier id="bookid">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>'
        "<dc:language>en</dc:language>"
        "</metadata>"
        "<manifest>"
        '<item id="ch1" href="chap1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="ch2" href="chap2.xhtml" media-type="application/xhtml+xml"/>'
        "</manifest>"
        "<spine>"
        '<itemref idref="ch1"/>'
        '<itemref idref="ch2"/>'
        "</spine>"
        "</package>"
    ).encode("utf-8")

    ch1 = (
        chapter_xhtml.encode("utf-8")
        if chapter_xhtml is not None
        else (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
            '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>First</title></head>'
            f"<body><h1>Chapter 1: Morning</h1><p>{_prose('Morning')}</p></body></html>"
        ).encode("utf-8")
    )
    ch2 = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Second</title></head>'
        f"<body><h1>Chapter 2: Evening</h1><p>{_prose('Evening')}</p></body></html>"
    ).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/chap1.xhtml", ch1)
        zf.writestr("OEBPS/chap2.xhtml", ch2)
        if extra_entries:
            for name, payload in extra_entries.items():
                zf.writestr(name, payload)
    return buf.getvalue()


def _stored_original_files() -> list[Path]:
    originals_dir = config.UPLOAD_DIR / "originals"
    if not originals_dir.exists():
        return []
    return [p for p in originals_dir.rglob("*") if p.is_file()]


def _staged_tmp_entries() -> list[Path]:
    tmp_dir = config.DATA_DIR / "tmp"
    if not tmp_dir.exists():
        return []
    return list(tmp_dir.iterdir())


def _assert_zero_user_books_and_artifacts(initial_book_ids: set[int]) -> None:
    current_ids = set(Book.objects.values_list("id", flat=True))
    assert current_ids == initial_book_ids
    assert not Chapter.objects.exclude(book_id__in=initial_book_ids).exists()
    assert _stored_original_files() == []
    assert _staged_tmp_entries() == []


def test_valid_epub_and_fb2_uploads_store_after_parse(data_dir, tmp_path):
    init_db()
    epub_path = tmp_path / "meditations.epub"
    epub_path.write_bytes(_valid_epub_bytes())
    epub_book = ingest_file(epub_path)
    assert epub_book["title"] == "Quiet Meditations"
    assert epub_book["author"] == "Marcus Test"
    assert epub_book["chapter_count"] >= 1

    fb2_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        "<description><title-info>"
        "<author><first-name>Seneca</first-name><last-name>Minor</last-name></author>"
        "<book-title>Letters on Time</book-title>"
        "</title-info></description>"
        "<body>"
        f"<section><title><p>Chapter 1. On Saving Time</p></title><p>{_prose('Saving time')}</p></section>"
        f"<section><title><p>Chapter 2. On Stillness</p></title><p>{_prose('Stillness')}</p></section>"
        "</body>"
        '<binary id="cover.jpg" content-type="image/jpeg">AAAA</binary>'
        "</FictionBook>"
    )
    fb2_path = tmp_path / "letters.fb2"
    fb2_path.write_text(fb2_xml, encoding="utf-8")
    fb2_book = ingest_file(fb2_path)
    assert fb2_book["title"] == "Letters on Time"
    assert fb2_book["author"] == "Seneca Minor"
    assert fb2_book["chapter_count"] >= 1
    assert len(_stored_original_files()) == 2


def test_epub_zip_bombs_traversal_nested_encrypted_and_corrupt_rejected(data_dir, tmp_path, monkeypatch):
    bootstrap()
    initial_ids = set(Book.objects.values_list("id", flat=True))

    # 1. Compression ratio bomb (highly repetitive data compressing > 100:1)
    ratio_bomb = _valid_epub_bytes(extra_entries={"OEBPS/bomb.txt": b"0" * (512 * 1024)})
    p_ratio = tmp_path / "ratio_bomb.epub"
    p_ratio.write_bytes(ratio_bomb)
    with pytest.raises(UploadValidationError) as exc_ratio:
        ingest_file(p_ratio)
    assert exc_ratio.value.code == "unsafe"
    _assert_zero_user_books_and_artifacts(initial_ids)

    # 2. Per-entry uncompressed byte cap
    with monkeypatch.context() as m:
        m.setattr(extract_mod, "MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES", 8 * 1024)
        incompressible = bytes((i * 73 + 19) % 251 for i in range(12 * 1024))
        p_entry_cap = tmp_path / "entry_cap.epub"
        p_entry_cap.write_bytes(_valid_epub_bytes(extra_entries={"OEBPS/big.bin": incompressible}))
        with pytest.raises(UploadValidationError) as exc_entry:
            ingest_file(p_entry_cap)
        assert exc_entry.value.code == "unsafe"
    _assert_zero_user_books_and_artifacts(initial_ids)

    # 3. Total uncompressed byte cap across multiple entries
    with monkeypatch.context() as m:
        m.setattr(extract_mod, "MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES", 16 * 1024)
        chunk_a = bytes((i * 31 + 7) % 251 for i in range(10 * 1024))
        chunk_b = bytes((i * 53 + 11) % 251 for i in range(10 * 1024))
        p_total = tmp_path / "total_cap.epub"
        p_total.write_bytes(
            _valid_epub_bytes(extra_entries={"OEBPS/a.bin": chunk_a, "OEBPS/b.bin": chunk_b})
        )
        with pytest.raises(UploadValidationError) as exc_total:
            ingest_file(p_total)
        assert exc_total.value.code == "unsafe"
    _assert_zero_user_books_and_artifacts(initial_ids)

    # 4. Spoofed central directory file_size header (lies about size, caught during stream read)
    with monkeypatch.context() as m:
        m.setattr(extract_mod, "MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES", 4 * 1024)
        payload = bytes((i * 97 + 3) % 251 for i in range(9 * 1024))
        raw_epub = bytearray(_valid_epub_bytes(extra_entries={"OEBPS/spoof.bin": payload}))
        # Patch uncompressed size of OEBPS/spoof.bin in ZIP central directory & local header to 128 bytes
        packed_real = struct.pack("<I", len(payload))
        packed_fake = struct.pack("<I", 128)
        assert packed_real in raw_epub
        spoofed_bytes = bytes(raw_epub).replace(packed_real, packed_fake)
        p_spoof = tmp_path / "spoofed.epub"
        p_spoof.write_bytes(spoofed_bytes)
        with pytest.raises(UploadValidationError):
            ingest_file(p_spoof)
    _assert_zero_user_books_and_artifacts(initial_ids)

    # 5. Entry count ceiling
    with monkeypatch.context() as m:
        m.setattr(extract_mod, "MAX_ZIP_ENTRIES", 6)
        many = {f"OEBPS/extra_{idx}.txt": f"item {idx}".encode() for idx in range(5)}
        p_many = tmp_path / "many_entries.epub"
        p_many.write_bytes(_valid_epub_bytes(extra_entries=many))
        with pytest.raises(UploadValidationError) as exc_many:
            ingest_file(p_many)
        assert exc_many.value.code == "unsafe"
    _assert_zero_user_books_and_artifacts(initial_ids)

    # 6. Path traversal (.., leading /, Windows backslash ..\)
    for bad_entry in ("../outside.xhtml", "OEBPS/../../etc/passwd", "/etc/passwd", "..\\evil.xhtml"):
        p_trav = tmp_path / "traversal.epub"
        p_trav.write_bytes(_valid_epub_bytes(extra_entries={bad_entry: b"traversal"}))
        with pytest.raises(UploadValidationError) as exc_trav:
            ingest_file(p_trav)
        assert exc_trav.value.code == "unsafe"
        _assert_zero_user_books_and_artifacts(initial_ids)

    # 7. Nested archive by filename (.zip) and by magic signature (disguised PK\x03\x04 inside .dat)
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as inner_zf:
        inner_zf.writestr("inner.txt", b"nested archive content")
    inner_zip = inner_buf.getvalue()

    for entry_name in ("OEBPS/nested.zip", "OEBPS/disguised_archive.dat"):
        p_nested = tmp_path / "nested.epub"
        p_nested.write_bytes(_valid_epub_bytes(extra_entries={entry_name: inner_zip}))
        with pytest.raises(UploadValidationError) as exc_nested:
            ingest_file(p_nested)
        assert exc_nested.value.code == "unsafe"
        _assert_zero_user_books_and_artifacts(initial_ids)

    # 8. Encrypted ZIP entry (general purpose bit 0 set)
    enc_buf = io.BytesIO()
    with zipfile.ZipFile(enc_buf, "w") as zf:
        zf.writestr("META-INF/container.xml", b"<container/>")
        zinfo = zipfile.ZipInfo("OEBPS/secret.xhtml")
        zinfo.flag_bits |= 0x1
        zf.writestr(zinfo, b"encrypted-bytes")
    p_enc = tmp_path / "encrypted.epub"
    p_enc.write_bytes(enc_buf.getvalue())
    with pytest.raises(UploadValidationError) as exc_enc:
        ingest_file(p_enc)
    assert exc_enc.value.code == "corrupt"
    _assert_zero_user_books_and_artifacts(initial_ids)

    # 9. Corrupt ZIP and ZIP missing META-INF/container.xml
    p_corrupt = tmp_path / "corrupt.epub"
    p_corrupt.write_bytes(b"PK\x03\x04not-a-valid-zip-archive-at-all")
    with pytest.raises(UploadValidationError) as exc_corrupt:
        ingest_file(p_corrupt)
    assert exc_corrupt.value.code == "corrupt"

    no_container_buf = io.BytesIO()
    with zipfile.ZipFile(no_container_buf, "w") as zf:
        zf.writestr("hello.txt", b"just a normal zip renamed to epub")
    p_not_epub = tmp_path / "plain_zip.epub"
    p_not_epub.write_bytes(no_container_buf.getvalue())
    with pytest.raises(UploadValidationError) as exc_no_cont:
        ingest_file(p_not_epub)
    assert exc_no_cont.value.code == "corrupt"
    _assert_zero_user_books_and_artifacts(initial_ids)


def test_mobi_malformed_and_temp_dir_ceilings_cleanup_guaranteed(data_dir, tmp_path, monkeypatch):
    import mobi

    bootstrap()
    initial_ids = set(Book.objects.values_list("id", flat=True))

    # 1. Invalid header .mobi (random garbage)
    bad_mobi = tmp_path / "garbage.mobi"
    bad_mobi.write_bytes(b"not a palm database")
    with pytest.raises(UploadValidationError) as exc_hdr:
        ingest_file(bad_mobi)
    assert exc_hdr.value.code == "corrupt"
    _assert_zero_user_books_and_artifacts(initial_ids)

    # 2. Truncated/corrupt PalmDB with BOOKMOBI magic at offset 60..68 -> unpackBook fails, temp dir cleaned up
    corrupt_palmdb = bytearray(128)
    corrupt_palmdb[60:68] = b"BOOKMOBI"
    corrupt_palmdb[76:78] = b"\x00\x05"  # claims 5 sections in truncated 128-byte file
    corrupt_mobi = tmp_path / "truncated_bookmobi.azw3"
    corrupt_mobi.write_bytes(bytes(corrupt_palmdb))
    with pytest.raises(UploadValidationError) as exc_unpack:
        ingest_file(corrupt_mobi)
    assert exc_unpack.value.code == "corrupt"
    _assert_zero_user_books_and_artifacts(initial_ids)

    # 3. Unpacked MOBI exceeding MAX_MOBI_EXTRACTED_BYTES -> rejected and temp dir removed in finally
    created_temp_dirs: list[Path] = []

    def fake_extract_huge_bytes(_infile: str):
        d = tmp_path / "fake_mobi_huge"
        d.mkdir(parents=True, exist_ok=True)
        created_temp_dirs.append(d)
        sub = d / "mobi7"
        sub.mkdir()
        book_html = sub / "book.html"
        book_html.write_bytes(b"<p>" + (b"word " * 4096) + b"</p>")
        return str(d), str(book_html)

    with monkeypatch.context() as m:
        m.setattr(mobi, "extract", fake_extract_huge_bytes)
        m.setattr(extract_mod, "MAX_MOBI_EXTRACTED_BYTES", 2048)
        sample_mobi = tmp_path / "bomb.mobi"
        sample_mobi.write_bytes(bytes(corrupt_palmdb))
        with pytest.raises(UploadValidationError) as exc_bytes:
            extract_path(sample_mobi)
        assert exc_bytes.value.code == "unsafe"
        assert created_temp_dirs and not created_temp_dirs[-1].exists()

    # 4. Unpacked MOBI exceeding MAX_MOBI_EXTRACTED_FILES -> rejected and temp dir removed in finally
    def fake_extract_many_files(_infile: str):
        d = tmp_path / "fake_mobi_many"
        d.mkdir(parents=True, exist_ok=True)
        created_temp_dirs.append(d)
        for idx in range(12):
            (d / f"img_{idx}.dat").write_bytes(b"abc")
        html = d / "book.html"
        html.write_text(f"<html><body><p>{_prose('Mobi')}</p></body></html>", encoding="utf-8")
        return str(d), str(html)

    with monkeypatch.context() as m:
        m.setattr(mobi, "extract", fake_extract_many_files)
        m.setattr(extract_mod, "MAX_MOBI_EXTRACTED_FILES", 5)
        sample_mobi2 = tmp_path / "manyfiles.azw"
        sample_mobi2.write_bytes(bytes(corrupt_palmdb))
        with pytest.raises(UploadValidationError) as exc_files:
            extract_path(sample_mobi2)
        assert exc_files.value.code == "unsafe"
        assert not created_temp_dirs[-1].exists()

    _assert_zero_user_books_and_artifacts(initial_ids)


def test_xxe_billion_laughs_external_dtd_and_malformed_fb2_html_epub_rejected(data_dir, tmp_path, monkeypatch):
    bootstrap()
    initial_ids = set(Book.objects.values_list("id", flat=True))
    secret_file = tmp_path / "secret_canary.txt"
    secret_file.write_text("TOP_SECRET_CANARY_VALUE_999", encoding="utf-8")

    xxe_fb2 = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<!DOCTYPE FictionBook [<!ENTITY xxe SYSTEM "file://{secret_file}">]>'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        f"<body><section><title><p>Ch 1</p></title><p>&xxe; {_prose('XXE')}</p></section></body>"
        "</FictionBook>"
    )
    billion_laughs_fb2 = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<!DOCTYPE lolz ["
        '<!ENTITY lol "lol">'
        '<!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        '<!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">'
        '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
        "]>"
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        f"<body><section><p>&lol3; {_prose('Laughs')}</p></section></body>"
        "</FictionBook>"
    )
    ext_dtd_fb2 = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE FictionBook SYSTEM "http://127.0.0.1:9/external.dtd">'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        f"<body><section><p>{_prose('External DTD')}</p></section></body>"
        "</FictionBook>"
    )
    xinclude_fb2 = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:xi="http://www.w3.org/2001/XInclude">'
        f'<body><section><p><xi:include href="file://{secret_file}" parse="text"/> {_prose("XInc")}</p></section></body>'
        "</FictionBook>"
    )
    malformed_fb2 = "<FictionBook><body><section><p>Unclosed XML tags"
    wrong_root_fb2 = f'<?xml version="1.0"?><note><body>{_prose("Wrong root")}</body></note>'

    for idx, payload in enumerate(
        (xxe_fb2, billion_laughs_fb2, ext_dtd_fb2, xinclude_fb2, malformed_fb2, wrong_root_fb2)
    ):
        fpath = tmp_path / f"case_{idx}.fb2"
        fpath.write_text(payload, encoding="utf-8")
        with pytest.raises(UploadValidationError):
            ingest_file(fpath)
        _assert_zero_user_books_and_artifacts(initial_ids)

    # HTML with XXE / external DTD
    xxe_html = (
        f'<!DOCTYPE html [<!ENTITY xxe SYSTEM "file://{secret_file}">]>'
        f"<html><body><h1>Ch 1</h1><p>&xxe; {_prose('HTML')}</p></body></html>"
    )
    html_path = tmp_path / "xxe.html"
    html_path.write_text(xxe_html, encoding="utf-8")
    with pytest.raises(UploadValidationError) as exc_html:
        ingest_file(html_path)
    assert exc_html.value.code == "unsafe"

    # EPUB containing XML entity expansion inside a chapter XHTML
    epub_entity_bytes = _valid_epub_bytes(
        chapter_xhtml=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<!DOCTYPE html [<!ENTITY xxe SYSTEM "file://{secret_file}">]>'
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            f"<body><h1>Ch 1</h1><p>&xxe; {_prose('EPUB')}</p></body></html>"
        )
    )
    epub_xxe_path = tmp_path / "xxe.epub"
    epub_xxe_path.write_bytes(epub_entity_bytes)
    with pytest.raises(UploadValidationError) as exc_epub:
        ingest_file(epub_xxe_path)
    assert exc_epub.value.code == "unsafe"

    # Markup / extracted text size cap enforcement
    with monkeypatch.context() as m:
        m.setattr(extract_mod, "MAX_RAW_MARKUP_BYTES", 1024)
        oversized_html = tmp_path / "oversized_markup.html"
        oversized_html.write_text(f"<html><body><p>{_prose('Big', 40)}</p></body></html>", encoding="utf-8")
        with pytest.raises(UploadValidationError) as exc_markup_cap:
            ingest_file(oversized_html)
        assert exc_markup_cap.value.code == "too_large"

    assert not Chapter.objects.filter(body__contains="TOP_SECRET_CANARY_VALUE_999").exists()
    _assert_zero_user_books_and_artifacts(initial_ids)


def test_atomic_parse_before_catalog_leaves_zero_rows_or_files_on_failure(data_dir, tmp_path, monkeypatch):
    bootstrap()
    monkeypatch.setenv("ONECHAPTER_UPLOAD_RATE_LIMIT_PER_READER", "20")
    monkeypatch.setenv("ONECHAPTER_UPLOAD_RATE_LIMIT_PER_IP", "20")
    initial_ids = set(Book.objects.values_list("id", flat=True))
    client = Client()

    # 1. Zero-chapter / empty-prose uploads across TXT, HTML, and FB2, plus disguised binary
    zero_chapter_cases = [
        ("whitespace.txt", b"   \n\n\t  ", "text/plain", "no_chapters"),
        ("too_few_words.txt", b"Just three words.", "text/plain", "no_chapters"),
        (
            "scripts_only.html",
            b"<html><head><style>body{}</style></head><body><script>var x = 1;</script></body></html>",
            "text/html",
            "no_chapters",
        ),
        (
            "empty_body.fb2",
            (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
                b"<description><title-info><book-title>Empty</book-title></title-info></description>"
                b"<body></body></FictionBook>"
            ),
            "application/xml",
            "no_chapters",
        ),
        ("disguised_pdf.txt", b"%PDF-1.7\n1 0 obj\n<<>>\nendobj", "text/plain", "unsupported"),
        ("malware.exe", b"MZ\x90\x00\x03\x00", "application/octet-stream", "unsupported"),
    ]

    for filename, content, mime, expected_notice in zero_chapter_cases:
        upload = SimpleUploadedFile(filename, content, content_type=mime)
        resp = client.post("/upload", {"file": upload})
        assert resp.status_code == 303
        assert f"notice={expected_notice}" in resp.headers["location"]
        followed = client.get(resp.headers["location"])
        assert followed.status_code == 200
        assert '<p class="flash">' in followed.content.decode()
        _assert_zero_user_books_and_artifacts(initial_ids)

    # 2. Verify S3 put_object is NEVER called when parsing/splitting fails,
    # and if S3 put_object itself raises after parsing, DB rows roll back atomically!
    monkeypatch.setenv("ONECHAPTER_S3_BUCKET", "onechapter-atomic-test")
    s3_puts: list[str] = []

    class FailingS3Client:
        def __init__(self, should_fail_put: bool = False) -> None:
            self.should_fail_put = should_fail_put

        def put_object(self, *, Bucket, Key, Body, ServerSideEncryption):
            s3_puts.append(Key)
            if self.should_fail_put:
                raise RuntimeError("Simulated S3 outage")

    bad_file = tmp_path / "unparseable.epub"
    bad_file.write_bytes(b"PK\x03\x04broken")
    with pytest.raises(UploadValidationError):
        ingest_file(bad_file, s3_client=FailingS3Client())
    assert s3_puts == []
    assert set(Book.objects.values_list("id", flat=True)) == initial_ids

    good_file = tmp_path / "valid_book.txt"
    good_file.write_text(f"# Chapter 1\n\n{_prose('Atomic one')}\n\n# Chapter 2\n\n{_prose('Atomic two')}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Simulated S3 outage"):
        ingest_file(good_file, s3_client=FailingS3Client(should_fail_put=True))
    assert len(s3_puts) == 1
    assert set(Book.objects.values_list("id", flat=True)) == initial_ids
    assert not Chapter.objects.exclude(book_id__in=initial_ids).exists()


def test_web_upload_rate_limiting_per_reader_and_per_ip(data_dir, monkeypatch):
    bootstrap()
    monkeypatch.setenv("DJANGO_DEBUG", "0")
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")
    monkeypatch.setenv("ONECHAPTER_UPLOAD_RATE_LIMIT_PER_READER", "2")
    monkeypatch.setenv("ONECHAPTER_UPLOAD_RATE_LIMIT_PER_IP", "3")
    monkeypatch.setenv("ONECHAPTER_UPLOAD_RATE_WINDOW_SECONDS", "600")

    alice = get_or_create_telegram_reader(None, 7001, "Alice")
    bob = get_or_create_telegram_reader(None, 7002, "Bob")

    alice_client = Client()
    alice_client.get(f"/auth/magic?token={create_magic_login_token(alice.id, 7001)}")

    bob_client = Client()
    bob_client.get(f"/auth/magic?token={create_magic_login_token(bob.id, 7002)}")

    def _post_small(c: Client, idx: int, ip: str) -> str:
        payload = f"# Chapter 1\n\n{_prose(f'Book {idx}')}".encode("utf-8")
        up = SimpleUploadedFile(f"book_{idx}.txt", payload, content_type="text/plain")
        res = c.post("/upload", {"file": up}, HTTP_CF_CONNECTING_IP=ip)
        assert res.status_code == 303
        return res.headers["location"]

    # Alice uploads twice from 203.0.113.10 -> both succeed
    assert _post_small(alice_client, 1, "203.0.113.10").startswith("/book/")
    assert _post_small(alice_client, 2, "203.0.113.10").startswith("/book/")

    # Alice's 3rd upload (even from a different IP!) hits per-reader limit (2)
    loc_reader_cap = _post_small(alice_client, 3, "203.0.113.99")
    assert loc_reader_cap == "/library?notice=rate_limited"
    rate_html = alice_client.get(loc_reader_cap).content.decode()
    assert "Too many upload attempts" in rate_html

    # Bob uploads from 203.0.113.10 -> 3rd upload from that IP succeeds (IP limit = 3)
    assert _post_small(bob_client, 4, "203.0.113.10").startswith("/book/")

    # Bob's next upload from 203.0.113.10 hits per-IP limit (3), while Bob still has reader quota on a new IP!
    assert _post_small(bob_client, 5, "203.0.113.10") == "/library?notice=rate_limited"
    assert _post_small(bob_client, 6, "198.51.100.77").startswith("/book/")


def test_telegram_on_document_20mb_limit_clean_errors_and_rate_limit(data_dir, monkeypatch):
    bootstrap()
    initial_ids = set(Book.objects.values_list("id", flat=True))
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")
    monkeypatch.setenv("ONECHAPTER_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))
    monkeypatch.setenv("ONECHAPTER_UPLOAD_RATE_LIMIT_PER_READER", "4")

    def _make_doc_update(user_id: int, file_name: str, file_size: int | None):
        replies: list[str] = []

        async def reply_text(text: str, **_kw):
            replies.append(text)

        doc = SimpleNamespace(file_name=file_name, file_size=file_size, file_id=f"fid-{file_name}")
        msg = SimpleNamespace(document=doc, reply_text=reply_text)
        user = SimpleNamespace(id=user_id, full_name="Reader", username="reader")
        return SimpleNamespace(effective_user=user, message=msg), replies

    async def scenario():
        get_file_calls: list[str] = []

        class FakeTgFile:
            def __init__(self, payload: bytes) -> None:
                self._payload = payload

            async def download_as_bytearray(self) -> bytearray:
                return bytearray(self._payload)

        class FakeContextBot:
            def __init__(self, payloads: dict[str, bytes | Exception]) -> None:
                self._payloads = payloads

            async def get_file(self, file_id: str):
                get_file_calls.append(file_id)
                item = self._payloads[file_id]
                if isinstance(item, Exception):
                    raise item
                return FakeTgFile(item)

        bot = FakeContextBot(
            {
                "fid-corrupt.epub": b"PK\x03\x04not-a-valid-epub",
                "fid-empty_chapters.txt": b"tiny stub",
                "fid-unknown_size_big.epub": RuntimeError("BadRequest: File is too big"),
            }
        )
        ctx = SimpleNamespace(bot=bot)

        # 1. 21 MiB file (<= 25 MiB web cap, > 20 MiB Telegram Bot API getFile cap) rejected BEFORE get_file!
        upd_21mb, replies_21mb = _make_doc_update(8101, "twenty_one_mb.epub", 21 * 1024 * 1024)
        await on_document(upd_21mb, ctx)
        assert get_file_calls == []
        assert any("20 MB" in r and "too large" in r for r in replies_21mb)

        # 2. Corrupt EPUB in Telegram -> clean user-facing error, zero DB rows or stored originals
        upd_bad, replies_bad = _make_doc_update(8101, "corrupt.epub", 20)
        await on_document(upd_bad, ctx)
        assert any("Could not import that file" in r for r in replies_bad)
        await asgiref_sync_to_async(_assert_zero_user_books_and_artifacts)(initial_ids)

        # 3. 0-chapter stub TXT in Telegram -> clean user-facing error, zero DB rows or stored originals
        upd_stub, replies_stub = _make_doc_update(8101, "empty_chapters.txt", 9)
        await on_document(upd_stub, ctx)
        assert any("No readable chapters" in r for r in replies_stub)
        await asgiref_sync_to_async(_assert_zero_user_books_and_artifacts)(initial_ids)

        # 4. Telegram getFile raising 'File is too big' when file_size metadata was omitted -> clean 20 MB reply
        upd_api_big, replies_api_big = _make_doc_update(8101, "unknown_size_big.epub", None)
        await on_document(upd_api_big, ctx)
        assert any("20 MB" in r for r in replies_api_big)

        # 5. 5th document from reader 8101 hits per-reader rate limit (4) before calling get_file!
        calls_before = len(get_file_calls)
        upd_rate, replies_rate = _make_doc_update(8101, "fifth.epub", 100)
        await on_document(upd_rate, ctx)
        assert len(get_file_calls) == calls_before
        assert any("Too many upload attempts" in r for r in replies_rate)

    asyncio.run(scenario())
    _assert_zero_user_books_and_artifacts(initial_ids)
