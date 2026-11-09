# ADR 0004: Aurora PostgreSQL serverless for reading state

- Status: Proposed
- Date: 2026-11-09

## Context

Books, chapters, readers, plans, deliveries, reflections, and settings are SQLite tables in `data/app.db`. Django itself has no models; the file is opened by `onechapter.db`. The SQL is SQLite-specific: `?` placeholders, `datetime('now')`, and `PRAGMA user_version`.

That file is the library and the reading calendar. It should survive replacement of the app server. Backups and restore should be a property of the database, not a cron copy of a disk.

The workload is small relational data: foreign keys, unique constraints, and ordinary joins. There is no need for a Postgres extension. The project is Free plan, region `eu-north-1` only, and should not add a NAT gateway so the app can reach a database inside a VPC.

## Decision

Use Aurora PostgreSQL serverless in `eu-north-1`, created with Express configuration. Express configuration has no VPC. The Free plan allowance for this engine is up to 4 ACUs and 1 GiB of storage per cluster, which covers chapter text for this app.

The app server connects with the PostgreSQL driver and IAM authentication, which Express configuration sets up for the administrator user. Schema creation and migrations move from SQLite `user_version` to a PostgreSQL schema version stored in the database.

Seeded public-domain books stay in the repository and are loaded by the existing seed path. They are not part of the upload archive.

## Consequences

- Replacing the EC2 instance does not lose plans, notes, or delivery history.
- The port is real work: placeholders, default timestamps, migrations, and the connection helper. Tests that open SQLite need a PostgreSQL path or a kept SQLite mode for local development. Local development can stay on SQLite until that port is done; production does not.
- 1 GiB is the Free plan storage cap per cluster. Chapter text fits. The cap is the signal to stop storing large binaries in the database, which is already the point of [ADR 0005](0005-s3-book-originals.md).
- Aurora restore brings back reading state. It does not bring back an uploaded file that was never stored in S3.

## Alternatives considered

- **Aurora DSQL.** Also in `eu-north-1`, no VPC, scales to zero with no resume wait, and includes a standing free allowance. Foreign keys work. The SQL dialect is narrower (no `SERIAL`, identity cache rules, asynchronous index creation). Use DSQL only if idle cost overtakes the simpler PostgreSQL port.
- **SQLite on the instance volume, with EBS snapshots.** Fewer code changes. The database still lives and dies with a volume, and restore is an operator procedure rather than a database backup.
- **RDS for PostgreSQL on a small always-on instance.** A full PostgreSQL, plus a VPC, and a server that bills while the app is idle. Express configuration avoids that floor.
