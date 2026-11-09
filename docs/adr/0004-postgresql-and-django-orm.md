# ADR 0004: PostgreSQL 17 in Docker with S3 backups and Django 5.2 ORM from Day 1

- Status: Proposed
- Date: 2026-11-09

## Context

Currently, `onechapter` stores books, chapters, readers, plans, deliveries, reflections, and settings in a local SQLite file (`data/app.db`) managed by a custom raw-SQL layer in `src/onechapter/db.py` (`sqlite3`, `?` placeholders, `PRAGMA user_version` migrations, and opening a new connection per `with db() as conn:` block), while `src/onechapter/project/settings.py` points Django's `DATABASES["default"]` at a dummy `:memory:` SQLite database.

Two critical problems surfaced during architecture review:

1. **Bypassing Django's ORM blocks future features:** One Chapter already runs on Django 5.2 LTS. Maintaining a hand-rolled SQL layer and custom migration runner in `onechapter.db` fights the framework and makes upcoming features—user accounts (`django.contrib.auth`), multi-channel identities, permissions, Django Admin, and rich reflections (notes and voice messages)—needlessly complex and error-prone. Because the app is not yet in public production, old local SQLite data can be discarded cleanly in favor of standard Django models and migrations from day 1.
2. **Aurora PostgreSQL Serverless v2 would exhaust the \$100 credit:** Under the AWS Free Plan, Aurora's "up to 4 ACUs and 1 GiB storage" is a maximum cap, not a free usage tier—every ACU-hour deducts from the project's \$100.00 credit. Worse, Aurora Serverless v2 requires at least 5 minutes of zero database activity to auto-pause (`0 ACU`). Because the delivery scheduler checks for due chapters every 60 seconds, an Aurora Serverless v2 cluster would never auto-pause, running at `>= 0.5 ACU` 24/7 (`~365 ACU-hours/month`, or **~\$45–\$51/month** in `eu-north-1`) and burning the entire \$100 credit in under 2 months. Even if the scheduler were changed, waking a paused `0 ACU` Aurora cluster adds a ~15-second cold-start stall to user requests.

## Decision

1. **Use Django 5.2 ORM and Migrations from Day 1:**
   - Replace the custom `src/onechapter/db.py` SQL layer and `:memory:` placeholder with real Django models (`django.db.models`) and standard Django migrations (`python manage.py migrate`).
   - Configure `DATABASES["default"]` via `DATABASE_URL` (using `psycopg` v3) with persistent connections (`CONN_MAX_AGE = 60`), eliminating per-query connection overhead.
   - Seeded public-domain books (`data/seed/`) are loaded idempotently via a Django management command (`python manage.py seed_books`) on startup/deploy.

2. **Run PostgreSQL 17 in Docker on the same EC2 instance, backed up to S3:**
   - Run `postgres:17-alpine` in `docker-compose.yml` on the same Graviton EC2 instance as the app ([ADR 0003](0003-one-app-server.md)).
   - Bind PostgreSQL strictly to the internal Docker bridge network (never exposed on a public port) with its data directory mounted on the instance's persistent EBS `gp3` volume.
   - Run an automated backup sidecar/timer (`pg_dump -Fc` or WAL-G) that writes compressed database snapshots to the private S3 bucket in `eu-north-1` under `backups/postgres/` (daily and automatically before every deploy/migration), with an S3 lifecycle rule expiring old daily snapshots after 30 days.
   - Provide a single restore command (`onechapter db-restore` / script) that pulls the latest `backups/postgres/` dump from S3 and restores it into the `db` container so the EC2 instance remains completely replaceable.

## Consequences

- **\$0.00/month database compute cost** and **sub-millisecond local queries with 0ms cold start**, keeping total monthly AWS spend around ~\$9–\$12/month so the \$100 credit lasts through `2027-05-06`.
- Full access to Django 5.2 features from day 1: `django.contrib.auth`, sessions, Django Admin for operator moderation, declarative model constraints, and standard schema migrations.
- Replacing the EC2 instance does not lose reading plans, reflections, or delivery history: the new instance restores the latest snapshot from `s3://<bucket>/backups/postgres/`.
- **Clean scale-up path:** Because the app connects via standard `DATABASE_URL` and Django's PostgreSQL backend, migrating to a managed RDS or Aurora PostgreSQL instance in the future (when revenue or scale justifies it) requires only taking a final `pg_dump`, restoring to RDS/Aurora, and updating `DATABASE_URL`—zero application code changes.

## Alternatives considered

- **Aurora PostgreSQL Serverless v2 (Express configuration).** Rejected for initial deployment because continuous 1-minute scheduler checks prevent `0 ACU` auto-pause (costing ~\$45–\$51/month and exhausting the \$100 credit in <2 months), and pausing to `0 ACU` causes ~15-second cold-start latency on web and Telegram requests.
- **Aurora DSQL.** Scales to zero without cold-start delay, but uses a restricted PostgreSQL subset (no foreign key constraints, async index requirements, and friction with standard Django ORM migrations).
- **Keep raw SQLite (`onechapter.db`) in production.** Forces hand-rolled SQL migrations and bypasses Django's ORM, auth, and admin ecosystem right as user accounts, multi-channel identities, and audio/text reflections are being introduced.
