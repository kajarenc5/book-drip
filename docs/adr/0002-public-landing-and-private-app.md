# ADR 0002: Public landing page, private reader

- Status: Proposed
- Date: 2026-11-09

## Context

The product people should find at `onechapter.am` is an explanation of the practice: one dense chapter a day, on the web and on Telegram. The running app is a different thing. It has no login. `/settings` can replace the bot token, `/upload` accepts books, and the reader is a single local user plus Telegram chats. Chapter text can include a book the operator owns and must not publish.

## Decision

Serve two hostnames.

| Hostname | What it is | Who can open it |
| --- | --- | --- |
| `onechapter.am` | Static landing page on Cloudflare | Anyone |
| `app.onechapter.am` | The Django reader, proxied to the app server | Cloudflare Access, allowlisted emails only |

The landing page is static files on Cloudflare (Workers static assets). It does not call the database, the bot, or S3. The reader and the bot stay on the app server in [ADR 0003](0003-one-app-server.md).

Access is the lock until the app has its own authentication. It is not a substitute for fixing the production settings in [ADR 0001](0001-cloudflare-edge.md).

## Consequences

- The landing page can ship before the reader is deployed.
- Book text, reflections, uploads, and the bot token are not on the public hostname.
- `/health` currently returns the bot username. On the deployed app it should report liveness only.
- Replacing Access later means adding real login to Django. The hostname split stays.

## Alternatives considered

- **One hostname for the marketing page and the reader.** The public page and the private tools would share a cache policy, an access policy, and a deploy. A mistake on either surface exposes the other.
- **A public reader with no Access.** The current views are an operator console: settings, upload, and the whole library.
