# ADR 0005: Original uploads live in S3

- Status: Proposed
- Date: 2026-11-09

## Context

A web upload or a Telegram document is written to `data/uploads`, then `ingest_file` splits it and stores chapters in the database. The book row keeps `source_filename` only. The parser needs a local path. After ingest, the durable content is the chapter text, and the original file is how that text can be built again.

The operator's own MOBI, EPUB, and FB2 files must be recoverable when the splitter changes or the chapter rows have to be recreated. They must not be public. Seeded books already live in the git repository and are out of scope.

Re-ingest today, when the slug and filename match, deletes that book's chapter rows and inserts them again. Plans, deliveries, and reflections point at a chapter position, not a chapter id, so they remain. A different split can attach them to a different chapter.

## Decision

Store each uploaded original in one private S3 General Purpose bucket in `eu-north-1`, storage class **S3 Standard**.

- Block Public Access on.
- Default encryption on (SSE-S3). Traffic uses HTTPS.
- The app's IAM role is the only writer and reader. Scope it to this bucket.
- Object key: `originals/{sha256}/{filename}`. The book row stores that key.
- Versioning on, so an overwrite can be undone. A lifecycle rule expires noncurrent versions. A second rule aborts incomplete multipart uploads, which otherwise keep billing.
- The server uploads the bytes, downloads them to a temporary file for the existing parser, ingests, stores the key, and deletes the temp file.
- A reprocess command lists the bucket or reads the stored key, downloads to a temp file, and runs the same ingest.

S3 Standard is the class because a reprocess is an immediate download. Infrequent Access and Glacier add a minimum billable size and a minimum storage duration, documented on the [S3 pricing page](https://aws.amazon.com/s3/pricing/). A few book files are a small charge next to the app server. Do not turn on cross-region replication.

## Consequences

- The instance disk no longer holds the only copy of an upload.
- If the books table is empty, the bucket is still the catalog: list `originals/` and ingest each object.
- Reprocess rebuilds chapter text. It does not rebuild the reading calendar or notes. Those come from Aurora backups ([ADR 0004](0004-aurora-postgresql.md)).
- Reprocess that changes chapter boundaries can leave a reflection or a delivery on the wrong position. The command should be deliberate, not automatic on every deploy.
- The same path serves the Library upload and a Telegram document.

## Alternatives considered

- **Keep originals on the EBS volume.** They disappear when the instance is replaced, which is the failure this decision exists to avoid.
- **Store the original bytes in Aurora.** Competes with the 1 GiB Free plan cap and mixes a file archive with reading state.
- **EFS mounted on the server.** A shared filesystem for one writer and rare reads. S3 matches "put a file, get it back later" without a mount.
