# Architecture Decision Records

Architecture decisions for deploying and evolving One Chapter at `onechapter.am`, `app.onechapter.am`, and `@one_chapter_bot`.

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-cloudflare-edge.md) | Accepted | `name.am` keeps domain registration; Cloudflare is DNS and the HTTPS edge (Origin CA + Caddy); Django serves static assets via WhiteNoise. |
| [0002](0002-public-landing-and-private-app.md) | Accepted | `onechapter.am` is a public static landing page on Cloudflare Workers (`site/`); `app.onechapter.am` is the reader (Stage 1 invite-only -> Stage 2 public). |
| [0003](0003-one-app-server.md) | Accepted | One Graviton EC2 instance (`t4g.small`) in `eu-north-1` runs Docker Compose (`proxy`, `onechapter` with `@one_chapter_bot`, `db`, and `db-backup`) within a ~\$9–\$12/month credit budget. |
| [0004](0004-postgresql-and-django-orm.md) | Accepted | Use Django 5.2 ORM & migrations backed exclusively by PostgreSQL 17 everywhere (local `data/postgres/`, CI, and Docker on EC2 with S3 backups). |
| [0005](0005-s3-book-originals.md) | Accepted | Store immutable book uploads (`originals/{sha256}/{filename}`), DB backups, and future audio media in a single private S3 bucket in `eu-north-1`; prevent user book overwrites. |
| [0006](0006-unified-identity-and-access-control.md) | Accepted | Unified `Reader` + `ChannelIdentity` model across Web and `@one_chapter_bot` (Login with Telegram first, extensible to Signal/WhatsApp/Email); shared library with anonymous uploader attribution. |
| [0007](0007-extensible-reflections-and-media.md) | Accepted | Reprocess-safe `chapter_key` and extensible `Reflection` schema supporting web & Telegram text notes today and voice/audio chapter reflections tomorrow. |
| [0008](0008-operator-privilege-isolation-and-account-blocking.md) | Accepted | Isolate operator privileges via `ONECHAPTER_OPERATOR_TELEGRAM_IDS` with fail-closed `/admin` access in public mode; add explicit `Reader.is_blocked` blocking and PostgreSQL 17 CI. |
| [0009](0009-upload-security-hardening-and-atomic-parse-before-save.md) | Accepted | Harden book uploads against archive/ZIP/MOBI/XML bombs, enforce per-reader/IP rate limits and 25 MiB/20 MiB ceilings, and parse/split atomically before saving to S3/DB. |
| [0010](0010-telegram-delivery-reliability-and-public-onboarding.md) | Accepted | Bulk due-delivery queries, automatic delivery disable on permanent Telegram errors, `RetryAfter` chunk resumption, `/stop` opt-out, browser timezone detection, reflection cap, and security headers. |
| [0011](0011-book-splitter-profiles-and-multilingual-extraction.md) | Accepted | Dedicated book splitter profiles (`tarasov`, `gosudar`, `generic`), paragraph-preserving HTML/FB2 extraction, ASCII transliterated slugs, and Cyrillic language detection. |

System diagrams, data model, and flows are in [../architecture/README.md](../architecture/README.md).
