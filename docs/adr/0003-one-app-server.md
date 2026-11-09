# ADR 0003: One Graviton EC2 server running Docker Compose for app, bot, and PostgreSQL

- Status: Proposed
- Date: 2026-11-09

## Context

One Chapter runs as an integrated Python service: Django 5.2 on ASGI (Uvicorn), the `@one_chapter_bot` Telegram runtime, and a scheduler that checks whether a reader's daily chapter is due in their local timezone. Delivery is calendar-based: if the process is briefly unavailable in the morning and recovers later the same local day, it still delivers today's chapter, and the `deliveries` table prevents duplicate sends.

The AWS project is constrained to the selected Region (`eu-north-1`) on the Free plan with **\$100.00 USD of credits through `2027-05-06`** (~6 months, meaning total AWS spend must stay well below ~\$16/month). AWS App Runner is unavailable in `eu-north-1`, and an Application Load Balancer, NAT Gateway, or always-on Aurora Serverless v2 cluster would exhaust the \$100 credit in 1–2 months.

At the same time, running Django views, Telegram handlers, and book ingestion on a single Uvicorn asyncio event loop requires care: synchronous CPU/disk work (parsing `.mobi`/`.epub`/`.fb2` files or uploading to S3) inside `async def` handlers will block the event loop and freeze both web requests and Telegram responses unless offloaded to a worker thread.

## Decision

Run **one Graviton burstable EC2 instance** (e.g., `t4g.small` or `t4g.micro`) in `eu-north-1`, in a public subnet with a static Public IPv4 (Elastic IP) and no load balancer or NAT Gateway.

Run the stack on the instance via **Docker Compose** (`restart: unless-stopped`):

1. **`proxy` (Caddy) or `cloudflared`:** Terminates TLS from Cloudflare on port `443` (or via Cloudflare Tunnel) and forwards requests to `app:43147` ([ADR 0001](0001-cloudflare-edge.md)).
2. **`app` (`onechapter serve`):** Runs Uvicorn with Django 5.2, the `@one_chapter_bot` Telegram bot, and the delivery scheduler.
3. **`db` (`postgres:17-alpine`):** Runs PostgreSQL 17 on an internal-only Docker network backed by the instance's EBS `gp3` volume, with automated backups to S3 ([ADR 0004](0004-postgresql-and-django-orm.md)).
4. **`db-backup`:** Lightweight scheduled container/timer that runs `pg_dump -Fc` (or WAL-G) and uploads compressed backups to S3 (`backups/postgres/`).

Operational and runtime rules:

- **Event-loop safety:** All CPU-bound or blocking I/O operations (`extract_path`, `split_extracted`, S3 `PutObject`/`GetObject`, and synchronous ORM queries inside `async` bot handlers) must run via `asyncio.to_thread()` or Django's `sync_to_async()` / async ORM methods so the Uvicorn event loop never stalls.
- **Single bot poller:** Only the `app` container polls Telegram (`@one_chapter_bot`) in production. Local development uses a separate dev bot token (or runs web-only without `TELEGRAM_BOT_TOKEN`) to avoid `409 Conflict` from concurrent `getUpdates` calls.
- **Disposable compute:** All durable state lives in S3 (`originals/`, `backups/postgres/`, and future `media/reflections/`). Replacing the EC2 instance is a `docker compose up -d` plus a one-command database restore from S3.

### Monthly Cost Budget in `eu-north-1` (against \$100.00 credit)

| Resource | Estimated Monthly Cost (`eu-north-1`) |
| --- | --- |
| EC2 `t4g.micro` (2 vCPU, 1 GiB) or `t4g.small` (2 vCPU, 2 GiB) | ~\$3.10 – \$6.20 |
| 1 in-use Public IPv4 / Elastic IP (`\$0.005/hr`) | \$3.65 |
| EBS `gp3` volume (20 GiB) | ~\$1.60 |
| S3 Standard (`originals/`, `backups/postgres/`, `media/`) | < \$0.25 |
| PostgreSQL 17 container on the same EC2 host | \$0.00 |
| **Total Monthly Burn** | **~\$8.60 – \$11.70 / month** |

At ~\$9–\$12/month, the \$100.00 USD credit comfortably covers the project through `2027-05-06` with headroom to spare.

## Consequences

- Restarting the `app` container during a deploy pauses web and bot traffic for a few seconds; same-day recovery still delivers any due chapters.
- Only one `app` container replica runs Telegram polling at a time. (When scale requires multiple web workers in the future, Telegram updates can switch to a webhook endpoint `/api/telegram/webhook` behind Cloudflare, and the scheduler can move to a separate worker process or EventBridge + Lambda.)
- Instance administration uses AWS Systems Manager Session Manager; the EC2 IAM instance profile grants least-privilege access strictly to SSM and the project's S3 bucket.

## Alternatives considered

- **Separate EC2 instance for the Telegram bot.** Doubles EC2 and IPv4 costs while sitting 99% idle.
- **ECS Express Mode / Fargate.** Managed load balancing and Fargate vCPU/memory pricing cost significantly more per month than a single Graviton instance.
- **Lambda for the web app and bot.** Incompatible with long-polling and unnecessary when a single Graviton instance stays within the Free plan credit budget.
