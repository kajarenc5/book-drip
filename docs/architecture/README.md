# Proposed architecture

Status: **Proposed**. These diagrams describe the deployment to review before implementation. The decisions are the ADRs in [../adr/README.md](../adr/README.md).

One Chapter stays a single Python process. Cloudflare is the public edge. AWS, in `eu-north-1` only, holds the reader, the bot, the reading database, and the original book files.

## System context

People read a public page at the apex. The reader and the operator tools are a second hostname. Telegram talks only to the app server.

```mermaid
flowchart TB
  person[Person]
  telegram[Telegram]

  subgraph edge [Cloudflare]
    site["onechapter.am<br/>static landing page"]
    access["app.onechapter.am<br/>Access, proxy, TLS"]
  end

  subgraph aws [AWS eu-north-1]
    app[App server<br/>reader and bot]
    db[Aurora PostgreSQL<br/>reading state]
    originals[S3<br/>uploaded books]
  end

  person --> site
  person --> access
  access -->|HTTPS 443| app
  app --> db
  app --> originals
  app <-->|poll and send| telegram
```

The landing page does not call AWS. Seeded public-domain books stay in the git repository and are loaded by the app, not stored in S3.

## What each store holds

| Store | Holds | Does not hold |
| --- | --- | --- |
| Cloudflare static site | The public explanation of the practice | Reader state, book text, tokens |
| Aurora PostgreSQL | Books, chapters, readers, plans, deliveries, reflections, settings | Original MOBI, EPUB, or FB2 bytes |
| S3 `originals/` | One object per uploaded file, key `originals/{sha256}/{filename}` | Reading progress and notes |
| App server disk | The container, temp files during ingest | The only copy of anything that must survive a rebuild |

## Deployment

name.am remains the registrar. Cloudflare's nameservers are authoritative. The app server has a public address and no load balancer. The security group accepts 443 from Cloudflare's published IP ranges only.

```mermaid
flowchart LR
  registrar[name.am]
  registrar -->|nameservers| cloudflare[Cloudflare DNS]

  visitor[Browser] --> cloudflare
  cloudflare -->|Full strict, port 443| proxy[TLS proxy]
  proxy --> process["onechapter serve :43147<br/>Django, poller, minute check"]

  process --> aurora[Aurora PostgreSQL serverless<br/>Express, no VPC]
  process --> bucket[S3 Standard bucket]
  process -->|outbound HTTPS| api[api.telegram.org]

  admin[Operator] -->|Session Manager| process
```

Production Django settings: debug off, a real secret key, `ALLOWED_HOSTS` and CSRF limited to `https://app.onechapter.am`, and the proxy SSL header set. HTML is not cached at Cloudflare. `/static/` is.

## Upload and reprocess

The parser still reads a local file. S3 is the copy that remains after the temp file is deleted.

```mermaid
sequenceDiagram
  participant Client as Web or Telegram
  participant App as App server
  participant Bucket as S3 originals
  participant DB as Aurora

  Client->>App: book file
  App->>Bucket: PutObject originals/sha256/filename
  App->>App: write temp file and ingest
  App->>DB: upsert book and chapters, store the object key
  App->>App: delete temp file
```

```mermaid
sequenceDiagram
  participant Op as Operator
  participant App as App server
  participant Bucket as S3 originals
  participant DB as Aurora

  Op->>App: reprocess
  App->>Bucket: GetObject for the stored key
  App->>App: ingest the temp file
  App->>DB: replace that book's chapter rows
  Note over DB: Plans, deliveries, and reflections stay.<br/>They are keyed by chapter position.
```

Reprocess is for a bad split or an empty chapter table. Restoring a day of reading, a note, or a "already sent" mark is an Aurora restore, not an S3 download.

## Delivery

The bot and the website share the process. Once a minute the process asks whether today's chapter is due and unsent. A restart later the same local day still sends it. A stop that lasts past local midnight misses that day.

A separate sender is deferred. If the morning message must survive the website being down, EventBridge Scheduler will call a Lambda that runs the same check. That Lambda is not part of the first deployment, and it is not a second server.

## Review notes

These are the choices worth challenging before implementation:

1. The reader is private via Cloudflare Access, not a new login. Access comes off only when Django grows its own authentication.
2. One EC2 instance is the compute. ECS Express Mode stays an alternative if its live price beats a single instance plus the time to patch it. The instance size waits on the live `eu-north-1` price so the credits last through 2027-05-06.
3. Aurora PostgreSQL serverless (Express) is the database. Aurora DSQL stays the alternative if idle cost becomes the reason to accept its narrower SQL dialect.
4. S3 holds originals only. Reprocessing can move notes onto a different chapter if the split positions change.
5. The first deployment does not add the Lambda sender. The bot runs on the app server.
