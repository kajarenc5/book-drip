# Proposed Architecture

Status: **Proposed**. This document describes the target deployment, data model, identity architecture, and operational flows for One Chapter (`onechapter.am` and `@one_chapter_bot`). Individual decisions are recorded in [../adr/README.md](../adr/README.md).

One Chapter runs as a cohesive Docker Compose stack on a single Graviton EC2 instance in `eu-north-1`, behind Cloudflare at the edge, using **Django 5.2 ORM** with **PostgreSQL 17** on the host and a single private **S3 Standard** bucket in `eu-north-1` for durable storage (book originals, automated PostgreSQL backups, and future audio reflections).

---

## 1. System Context

Visitors read the public explanation of the practice at `onechapter.am`. The web reader and library live at `app.onechapter.am`. Readers interact with the shared Telegram bot `@one_chapter_bot` (with the data model ready to add Signal and WhatsApp channels later). Both web and bot resolve to the same canonical `Reader` account.

```mermaid
flowchart TB
  person["Reader (Browser / Telegram)"]
  telegram["Telegram API<br/>@one_chapter_bot"]
  future_channels["Future Channels<br/>Signal / WhatsApp"]

  subgraph edge ["Cloudflare Edge"]
    site["onechapter.am<br/>Static landing page"]
    access["app.onechapter.am<br/>TLS, Cache (/static/), Stage 1 Access"]
  end

  subgraph aws ["AWS Selected Region: eu-north-1"]
    subgraph ec2 ["Graviton EC2 Instance (Docker Compose)"]
      proxy["Caddy / cloudflared<br/>TLS termination"]
      app["Django 5.2 + Bot Runtime<br/>Uvicorn :43147 + Scheduler"]
      db[("PostgreSQL 17<br/>Internal Docker Network")]
      backup["pg_dump / WAL-G<br/>Backup Timer"]
    end

    subgraph s3 ["S3 Standard Bucket (Private)"]
      originals["originals/{sha256}/{filename}"]
      db_backups["backups/postgres/{timestamp}.dump"]
      media["media/reflections/{reader_id}/{uuid}.{ext}"]
    end
  end

  person --> site
  person --> access
  person <--> telegram
  person -.-> future_channels

  access -->|"HTTPS 443 (Full strict)"| proxy
  proxy --> app
  app -->|"Django ORM (CONN_MAX_AGE)"| db
  backup --> db
  backup -->|"PutObject"| db_backups
  app -->|"PutObject / GetObject"| originals
  app -.->|"Presigned URLs / PutObject (future)"| media
  app <-->|"Outbound HTTPS"| telegram
  app <-.->|"Channel Adapter (future)"| future_channels
```

---

## 2. What Each Store Holds

| Store | Holds | Does Not Hold |
| --- | --- | --- |
| **Cloudflare Static Site** (`onechapter.am`) | Public explanation of the slow-reading practice, links to `@one_chapter_bot` and `app.onechapter.am` | Reader state, book text, credentials |
| **PostgreSQL 17** (`db` container on EBS `gp3`) | Users/Readers, `ChannelIdentity` links, books metadata, chapters (with `chapter_key`), reading plans, deliveries, reflections | Raw `.mobi`/`.epub`/`.fb2` binaries, raw audio blobs |
| **S3 `originals/`** | One immutable object per unique uploaded book file: `originals/{sha256}/{filename}` | Reading progress, notes |
| **S3 `backups/postgres/`** | Automated daily and pre-deploy compressed `pg_dump` / WAL archives (30-day retention) | Unencrypted public objects |
| **S3 `media/reflections/`** *(future)* | Voice/audio chapter reflections (`media/reflections/{reader_id}/{uuid}.{ext}`) served via short-lived Presigned URLs | Publicly listable files |
| **EC2 Ephemeral Disk (`/tmp`)** | Temporary scratch files during book ingestion or backup creation | Any state that lacks an S3 backup or original |

---

## 3. Domain Data Model (Django 5.2 ORM)

Instead of bypassing Django with raw SQLite queries, One Chapter uses standard Django 5.2 models and migrations ([ADR 0004](../adr/0004-postgresql-and-django-orm.md), [ADR 0006](../adr/0006-unified-identity-and-access-control.md), [ADR 0007](../adr/0007-extensible-reflections-and-media.md)).

```mermaid
erDiagram
  USER ||--|| READER : "has profile"
  READER ||--o{ CHANNEL_IDENTITY : "authenticates & receives via"
  READER ||--o{ PLAN : "reads"
  READER ||--o{ REFLECTION : "records"
  READER ||--o{ BOOK : "uploaded_by (internal audit only)"

  BOOK ||--|{ CHAPTER : "contains"
  BOOK ||--o{ PLAN : "scheduled by"
  BOOK ||--o{ REFLECTION : "referenced by"

  PLAN ||--o{ DELIVERY : "logs daily sends"
  PLAN ||--o{ REFLECTION : "optional pass link"
  CHAPTER ||--o{ REFLECTION : "anchored to"

  READER {
    bigint id PK
    bigint user_id FK "django.contrib.auth User"
    string display_name
    string timezone "default Asia/Yerevan"
    string delivery_hhmm "default 08:00"
    boolean is_allowed "Invite-only gate"
    datetime created_at
  }

  CHANNEL_IDENTITY {
    bigint id PK
    bigint reader_id FK
    string channel "telegram | signal | whatsapp | email"
    string external_id "UNIQUE (channel, external_id)"
    string handle "optional @username"
    boolean is_delivery_enabled
    jsonb metadata
    datetime verified_at
  }

  BOOK {
    bigint id PK
    string slug UK
    string title
    string author
    string language
    string profile "tarasov | prince | generic | seed"
    string source_filename
    string source_sha256 UK "nullable for seeds"
    string s3_key "originals/{sha256}/{filename}"
    bigint uploaded_by_id FK "nullable, hidden from public/readers"
    datetime created_at
  }

  CHAPTER {
    bigint id PK
    bigint book_id FK
    int position "UNIQUE (book_id, position)"
    string chapter_key "UNIQUE (book_id, chapter_key)"
    string content_sha256
    string part
    string title
    text body
    int word_count
  }

  PLAN {
    bigint id PK
    bigint reader_id FK
    bigint book_id FK
    date started_on
    date paused_on
    string status "idle | active | paused | finished"
  }

  DELIVERY {
    bigint id PK
    bigint plan_id FK
    date local_date
    int chapter_position
    string channel "telegram | signal | whatsapp | web"
    datetime delivered_at
  }

  REFLECTION {
    uuid id PK
    bigint reader_id FK
    bigint book_id FK
    bigint plan_id FK "nullable"
    bigint chapter_id FK "nullable ON DELETE SET NULL"
    string chapter_key "survives reprocess"
    int chapter_position_snapshot
    string chapter_title_snapshot
    string kind "text | audio"
    string source_channel "web | telegram | signal | whatsapp"
    text body
    string audio_s3_key "nullable: media/reflections/..."
    string audio_mime_type "nullable"
    int audio_duration_sec "nullable"
    text transcript_text "nullable (future STT)"
    datetime created_at
  }
```

### Key Data Model Properties

1. **Unified Identity (`Reader` + `ChannelIdentity`):**
   - A reader who starts `@one_chapter_bot` on Telegram and signs into `app.onechapter.am` via **Login with Telegram** uses the **exact same `Reader` row**.
   - Adding Signal, WhatsApp, or Email/Google login in the future adds a row to `ChannelIdentity` without altering `Reader`, `Plan`, or `Reflection`.
2. **Shared Library with Anonymous Uploader Attribution & No User Overwrites:**
   - Any authorized reader (`is_allowed = True`) can upload a new book or read any book in `Book`.
   - `Book.uploaded_by` is stored strictly for internal operator audit and is **never** exposed in web templates, JSON responses, or Telegram messages.
   - Regular readers cannot overwrite or delete existing books; uploading an already-known `source_sha256` (or canonical book slug) reuses the existing `Book`.
3. **Reprocess-Safe Reflections (`chapter_key` + Snapshots):**
   - `Reflection` links to `(reader, book, chapter)` and stores `chapter_key`, `chapter_position_snapshot`, and `chapter_title_snapshot`.
   - When an operator reprocesses a book, chapters are updated in place by `chapter_key` so notes and future voice memos never shift onto the wrong chapter or get cascade-deleted.

---

## 4. Core Operational Flows

### 4.1 Unified Web Login via Telegram (`@one_chapter_bot`)

```mermaid
sequenceDiagram
  participant User as Reader (Browser / Telegram)
  participant Web as app.onechapter.am (Django)
  participant Bot as @one_chapter_bot
  participant DB as PostgreSQL 17

  alt Telegram Login Widget on Web
    User->>Web: Click "Sign in with Telegram"
    Web->>Web: Verify HMAC-SHA256 signature with TELEGRAM_BOT_TOKEN
    Web->>DB: Lookup/Create ChannelIdentity(channel="telegram", external_id=tg_id) -> Reader
    Web-->>User: Set secure session cookie, redirect to /
  else Magic Link from @one_chapter_bot (/login)
    User->>Bot: /login
    Bot->>DB: Lookup Reader via ChannelIdentity(channel="telegram", external_id=tg_id)
    Bot-->>User: One-time signed login URL (short TTL)
    User->>Web: Open login URL
    Web->>DB: Consume token, create Django session for Reader
    Web-->>User: Redirect to /
  end
```

### 4.2 Book Upload (Deduplicated, Non-Destructive) & Operator Reprocess

```mermaid
sequenceDiagram
  participant Reader as Reader (Web or @one_chapter_bot)
  participant App as Django / Bot Worker Thread
  participant S3 as S3 Bucket (originals/)
  participant DB as PostgreSQL 17

  Reader->>App: Upload book file (.mobi / .epub / .fb2)
  App->>App: Compute sha256(bytes)
  App->>DB: Check Book by source_sha256
  alt Book with sha256 already exists
    DB-->>App: Existing Book
    App-->>Reader: Open/Start existing Book (no overwrite)
  else New file
    App->>App: Extract & split in worker thread (asyncio.to_thread)
    App->>S3: PutObject originals/{sha256}/{filename}
    App->>DB: INSERT Book (s3_key, source_sha256, uploaded_by) + Chapters (chapter_key)
    App->>App: Delete temp file
    App-->>Reader: Book ready in shared library
  end
```

```mermaid
sequenceDiagram
  participant Op as Operator (CLI / Admin)
  participant App as App Server
  participant S3 as S3 Bucket (originals/)
  participant DB as PostgreSQL 17

  Op->>App: onechapter reprocess <slug>
  App->>DB: Fetch Book.s3_key
  App->>S3: GetObject originals/{sha256}/{filename}
  App->>App: Re-split temp file -> chapters with chapter_key
  App->>DB: Upsert Chapters in place by (book_id, chapter_key)
  App->>DB: Re-link any Reflections by (book_id, chapter_key)
  Note over DB: Plans, Deliveries, and Reflections remain intact.<br/>Chapter title/position snapshots preserve historical context.
```

---

## 5. Deployment, Networking, and Cost Summary

- **Registrar & Edge ([ADR 0001](../adr/0001-cloudflare-edge.md), [ADR 0002](../adr/0002-public-landing-and-private-app.md)):**
  - `name.am` delegates authoritative DNS to Cloudflare.
  - `onechapter.am` is served directly from Cloudflare static assets.
  - `app.onechapter.am` proxies (`Full (strict)`) to the EC2 instance in `eu-north-1`. Static files (`/static/*`) are fingerprinted and served by `WhiteNoise` and cached at Cloudflare; dynamic HTML is never cached.
- **Compute & Database ([ADR 0003](../adr/0003-one-app-server.md), [ADR 0004](../adr/0004-postgresql-and-django-orm.md)):**
  - One Graviton EC2 instance (`t4g.micro` or `t4g.small`) in `eu-north-1` runs Docker Compose (`proxy`, `app`, `db`, `db-backup`).
  - Total estimated AWS spend is **~\$8.60–\$11.70/month** (EC2 + 1 Public IPv4 + 20 GiB EBS `gp3` + S3 Standard), comfortably fitting within the **\$100.00 USD Free Plan credit through `2027-05-06`** while avoiding Aurora Serverless v2's ~\$45+/month unpaused floor and 15-second cold starts.
  - AWS Systems Manager Session Manager is used for host operations (no inbound SSH port 22).
