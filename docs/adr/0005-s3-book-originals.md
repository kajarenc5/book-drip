# ADR 0005: S3 storage layout, immutable book uploads, and safe reprocessing

- Status: Proposed
- Date: 2026-11-09

## Context

When a book file (`.mobi`, `.epub`, `.fb2`, `.txt`, `.html`) is uploaded via the web Library or sent to `@one_chapter_bot` on Telegram, the parser (`extract_path` + `split_extracted`) requires a local file path to extract and split the book into daily chapters.

Previously, uploaded files were written only to local disk (`data/uploads/`), and re-uploading a book with the same slug and filename ran `DELETE FROM chapters WHERE book_id = ?` followed by a fresh insert—allowing any user to overwrite an existing book and risking orphaned or shifted reflections if chapter positions changed.

In the deployed architecture, the single private S3 bucket in `eu-north-1` serves as the durable object store for the entire system: original book files, PostgreSQL backups ([ADR 0004](0004-postgresql-and-django-orm.md)), and future user media such as voice reflections ([ADR 0007](0007-extensible-reflections-and-media.md)).

## Decision

Create one private S3 General Purpose bucket in `eu-north-1` using storage class **S3 Standard** with a structured key prefix layout:

| Prefix | Contents | Retention / Mutability |
| --- | --- | --- |
| `originals/{sha256}/{filename}` | Uploaded book files (`.mobi`, `.epub`, `.fb2`, etc.) | Immutable; retained permanently |
| `backups/postgres/{timestamp}.dump` | Automated PostgreSQL `pg_dump` / WAL archives ([ADR 0004](0004-postgresql-and-django-orm.md)) | Lifecycle rule expires noncurrent/old daily backups after 30 days |
| `media/reflections/{reader_id}/{uuid}.{ext}` | Future voice/audio chapter reflections ([ADR 0007](0007-extensible-reflections-and-media.md)) | Retained with the reflection; served via short-lived Presigned GET URLs |

Bucket security and lifecycle configuration:

- **Block Public Access:** Enabled (`BlockPublicAcls`, `IgnorePublicAcls`, `BlockPublicPolicy`, `RestrictPublicBuckets`).
- **Encryption:** Default encryption enabled (`SSE-S3`), HTTPS-only bucket policy (`aws:SecureTransport`).
- **Access control:** Only the EC2 instance IAM role (and local operator profile `book-drip-local`) can read/write the bucket.
- **Versioning & Multipart cleanup:** Versioning enabled; lifecycle rules expire noncurrent versions after 30 days and abort incomplete multipart uploads after 7 days.

### Upload, Deduplication, and Operator-Only Reprocessing Rules

1. **Content-addressed deduplication (no user overwrites):**
   - On upload (Web or Telegram), the server computes the file's `sha256` digest.
   - If a `Book` with `source_sha256 = sha256` already exists in PostgreSQL, the server **does not overwrite or re-ingest** the book; it immediately returns/starts the existing `Book`.
   - If a new file produces a title `slug` that collides with an existing book of a *different* `sha256`, regular reader uploads never overwrite the existing book: if it is the same canonical book (e.g., Tarasov's `tehnologiya-zhizni` already in the library), the existing book is used; otherwise a disambiguated slug (`{slug}-2`) is created.
   - For a genuinely new book, the server uploads the bytes to `s3://<bucket>/originals/{sha256}/{filename}`, runs ingestion on a temporary file in a worker thread (`asyncio.to_thread`), inserts the `Book` (storing `s3_key` and `source_sha256`) and its `Chapter` rows in a database transaction, and deletes the temporary file.

2. **Operator-only reprocess (`onechapter reprocess <slug>`):**
   - Only an operator (via CLI/Django Admin) can trigger reprocessing of an existing book when splitter logic improves.
   - Reprocess downloads `s3_key` from S3 to a temp file, runs the updated splitter, and **upserts `Chapter` rows in place** matching by stable `chapter_key` ([ADR 0007](0007-extensible-reflections-and-media.md)) rather than blindly deleting all chapter rows.

## Consequences

- No durable state lives exclusively on the EC2 instance's local filesystem; both the book archive (`originals/`) and database backups (`backups/postgres/`) live in S3.
- Regular readers can freely contribute books to the shared library without any risk of overwriting or corrupting an existing book that other readers are currently reading ([ADR 0006](0006-unified-identity-and-access-control.md)).
- S3 Standard avoids minimum object size and minimum duration penalties from S3-IA/Glacier while costing only cents per month for book files and compressed DB dumps.

## Alternatives considered

- **Keep uploads only on the EC2 EBS volume.** Replacing the EC2 instance would lose the original `.mobi`/`.epub` files needed to re-split books or rebuild from scratch.
- **Store book binary files (`BYTEA`) inside PostgreSQL.** Bloats database backups (`pg_dump`) and memory usage with static multi-megabyte files that belong in object storage.
- **Allow re-uploads with the same filename to replace chapter rows automatically.** Unsafe in a multi-user shared library; a second user uploading a different edition or malformed file would overwrite the book for everyone.
