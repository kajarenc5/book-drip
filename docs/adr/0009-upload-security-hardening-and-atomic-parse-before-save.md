# ADR 0009: Defense-in-depth upload hardening and atomic parse-before-save

- Status: Accepted (implemented in PR #11: `src/onechapter/ingest/extract.py`, `src/onechapter/library.py`, `src/onechapter/storage.py`, `src/onechapter/bot/handlers.py`, `src/onechapter/reader/views.py`, `infra/docker/Caddyfile`, `tests/test_upload_security.py`)
- Date: 2026-11-18

## Context

Any authorized reader can upload `.mobi`, `.azw`, `.azw3`, `.epub`, `.fb2`, `.html`, `.htm`, `.txt`, or `.md` files via the web Library or `@one_chapter_bot`. Opening uploads to external readers in a shared library introduces untrusted-archive and XML parsing attack vectors:

- **Archive bombs & disk exhaustion:** Malicious EPUB (ZIP) or MOBI files can trigger compression-ratio bombs, huge uncompressed entries, spoofed ZIP headers, nested archives, symlinks, or path traversal outside staging directories.
- **XML parser attacks:** Crafted FB2, XHTML, or EPUB OPF/NCX markup can attempt XXE, external DTD fetches, Billion Laughs entity expansion, or XInclude injection.
- **Partial state / orphan artifacts:** Saving uploaded files to S3 or `data/uploads/` before parsing and splitting left orphaned objects on disk/S3 when invalid or malicious files failed ingestion.
- **Upload flooding:** Repeated large uploads from a single reader or IP could tie up worker threads and bandwidth.

## Decision

1. **Layered Upload Size Ceilings:**
   - **Reverse proxy (`Caddyfile`):** Enforce `request_body { max_size 26MB }` at the edge proxy.
   - **Web (`config.py` / `settings.py`):** Default `ONECHAPTER_MAX_UPLOAD_BYTES = 25 MiB` (aligned with `DATA_UPLOAD_MAX_MEMORY_SIZE` and `FILE_UPLOAD_MAX_MEMORY_SIZE`), rejecting empty or oversized uploads with clear messages.
   - **Telegram (`bot/handlers.py`):** Enforce `ONECHAPTER_TELEGRAM_MAX_UPLOAD_BYTES = 20 MiB` (matching Telegram Bot API `getFile` ceiling) before and after download, guiding readers to the web reader for 20–25 MiB files.

2. **Strict Archive, Binary, and XML/HTML Validation (`ingest/extract.py`):**
   - Reject disguised binaries (ELF, PDF, images, RAR/7z/gzip/bzip2/xz, SQLite) and unsupported extensions before parsing.
   - **EPUB / ZIP safety:** Stream-validate entries without trusting central directory headers, enforcing `MAX_ZIP_ENTRIES = 2,000`, `MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES = 20 MiB`, `MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES = 64 MiB`, `MAX_ZIP_COMPRESSION_RATIO = 100.0`, and rejecting encrypted entries, path traversal, symlinks, and nested archives (by suffix and magic signature).
   - **MOBI safety:** Extract into an isolated temp directory with guaranteed cleanup (`finally`), enforcing `MAX_MOBI_EXTRACTED_FILES = 2,000` and `MAX_MOBI_EXTRACTED_BYTES = 64 MiB`.
   - **Markup & text safety:** Reject `<!ENTITY`, unsafe `<!DOCTYPE ... SYSTEM/PUBLIC>` URIs, and XInclude declarations across XML/FB2/EPUB/HTML, and enforce `MAX_RAW_MARKUP_BYTES = 16 MiB` and `MAX_EXTRACTED_TEXT_CHARS = 8 MiB`.
   - Raise typed `UploadValidationError(user_message, code=...)` so web and Telegram handlers return safe, actionable user errors without leaking internals.

3. **Atomic Parse-Before-Save (`library.py` & `storage.py`):**
   - `ingest_file()` validates the file, computes `source_sha256`, deduplicates against existing books, extracts text, splits chapters, and validates non-empty chapters **strictly before** storing the original in S3/`uploads/` or creating `Book`/`Chapter` rows.
   - Inside `transaction.atomic()`, `Book` and `Chapter` rows are created and the original is persisted via `persist_original`; if the transaction fails after persisting, `rollback_original` deletes the newly created S3/disk object so failed uploads leave zero DB rows and zero orphaned files.
   - Staged temp uploads are always removed in `finally` blocks (`cleanup_staged_upload`).

4. **Sliding-Window Upload Rate Limiting (`library.py`):**
   - Enforce in-memory sliding-window upload rate limits per reader (`ONECHAPTER_UPLOAD_RATE_LIMIT_PER_READER = 5`) and per client IP (`ONECHAPTER_UPLOAD_RATE_LIMIT_PER_IP = 10`) per `ONECHAPTER_UPLOAD_RATE_WINDOW_SECONDS = 600` (10 minutes) across both Web and Telegram.

## Consequences

- Invalid, corrupt, encrypted, or malicious uploads fail fast with clean user-facing errors and leave zero database rows, zero S3 objects, and zero leftover temp files.
- Upload memory, CPU, and disk usage stay bounded on a single `t4g.small` EC2 instance.
- Rate limits are process-local (sufficient for the single-instance deployment in [ADR 0003](0003-one-app-server.md)); if the app scales horizontally later, rate-limit counters can move to PostgreSQL or Redis.

## Alternatives considered

- **Store originals in S3 first, then parse asynchronously.** Leaves untrusted/malicious payloads in S3 and requires background cleanup jobs and asynchronous error notifications.
- **Rely solely on `ebooklib` / `mobi` / `lxml` defaults.** Default parsers do not bound ZIP decompression ratios, total extracted size, or temp-directory file counts and can trigger OOM or disk exhaustion on a 2 GiB instance.
