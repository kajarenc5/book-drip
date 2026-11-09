# ADR 0003: One app server for the reader and the bot

- Status: Proposed
- Date: 2026-11-09

## Context

One Chapter already runs as one process: Django on port 43147, Telegram long-polling, and a scheduler that checks once a minute whether today's chapter is due. Delivery is calendar-based. If the process is down in the morning and returns later the same local day, it still sends today's chapter, and the `deliveries` row stops it from sending that chapter twice.

The process is Python, not Node, and it listens for the whole time the bot is connected. Lambda Web Functions do not run this shape. App Runner is closed to new customers and is not a target. A load balancer or a NAT gateway would cost more each month than the application itself and would consume the project's credits.

The database and the original files will live outside the server ([ADR 0004](0004-aurora-postgresql.md), [ADR 0005](0005-s3-book-originals.md)), so the server can be replaced without taking the library with it.

## Decision

Run one Graviton burstable EC2 instance in `eu-north-1`, in a public subnet, with an Elastic IP. The existing container runs on it, behind the TLS proxy from [ADR 0001](0001-cloudflare-edge.md). Docker restarts unless stopped. There is no load balancer and no NAT gateway. Outbound HTTPS reaches Telegram and AWS APIs directly.

The instance size is chosen at launch from the live `eu-north-1` price, so that one instance fits the remaining credits through 2027-05-06. Confirm that price before creating the instance.

The bot stays inside this process for the first deployment: polling for commands and file uploads, and the one-minute delivery check.

## Consequences

- A website deploy or an instance stop also stops commands and delivery until the process is back. Same-day recovery still sends today's chapter.
- One process owns the bot connection, so two copies must not poll at once.
- Patch the OS, or replace the instance. The durable data is not on its disk.
- Session Manager is the admin path. The security group allows 443 only from Cloudflare's published ranges.

## Later, not in this decision

If the morning message must go out while the website is stopped, add EventBridge Scheduler invoking a small Python Lambda once a minute. That function runs the same due-chapter check against Aurora and calls the Telegram HTTP API. Commands and book uploads stay on the app server until a webhook is worth building. Do not add a second EC2 instance for the bot.

## Alternatives considered

- **A second always-on server for Telegram.** It would sit idle between messages and still need the same database and a lock against double delivery.
- **ECS Express Mode.** AWS would run the container and attach HTTPS. That managed door is the expensive part. Revisit only if the live price is acceptable next to a single instance.
- **Django on Lambda for the website.** The bot's long poll and the current ASGI server do not fit a short invocation. Revisit only if the website must cost nothing while nobody is reading.
