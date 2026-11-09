# Architecture Decision Records

Proposed decisions for deploying and evolving One Chapter at `onechapter.am` and `@one_chapter_bot`. Status stays **Proposed** until implementation merges.

| ADR | Decision |
| --- | --- |
| [0001](0001-cloudflare-edge.md) | `name.am` keeps domain registration; Cloudflare is DNS and the HTTPS edge; Django serves static assets via WhiteNoise. |
| [0002](0002-public-landing-and-private-app.md) | `onechapter.am` is a public static landing page; `app.onechapter.am` is the reader (gated by Cloudflare Access in Stage 1, opening to public sign-up in Stage 2). |
| [0003](0003-one-app-server.md) | One Graviton EC2 instance in `eu-north-1` runs Docker Compose (`proxy`, `app` with `@one_chapter_bot`, `db`, and `db-backup`) within a ~\$9–\$12/month credit budget. |
| [0004](0004-postgresql-and-django-orm.md) | Use Django 5.2 ORM & migrations from day 1, backed by PostgreSQL 17 in Docker on the EC2 host with automated backups to S3 (`backups/postgres/`). |
| [0005](0005-s3-book-originals.md) | Store immutable book uploads (`originals/{sha256}/{filename}`), DB backups, and future audio media in a single private S3 bucket in `eu-north-1`; prevent user book overwrites. |
| [0006](0006-unified-identity-and-access-control.md) | Unified `Reader` + `ChannelIdentity` model across Web and `@one_chapter_bot` (Login with Telegram first, extensible to Signal/WhatsApp/Email); shared library with anonymous uploader attribution. |
| [0007](0007-extensible-reflections-and-media.md) | Reprocess-safe `chapter_key` and extensible `Reflection` schema supporting text notes today and voice/audio chapter reflections tomorrow. |

System diagrams, data model, and flows are in [../architecture/README.md](../architecture/README.md).
