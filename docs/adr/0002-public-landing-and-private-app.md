# ADR 0002: Public landing page and staged access for the reader

- Status: Proposed
- Date: 2026-11-09

## Context

The public entry point at `onechapter.am` should explain the slow-reading practice: one short, dense chapter per day, delivered on Telegram (`@one_chapter_bot`) and in a quiet web reader, with tomorrow's chapter locked until tomorrow.

The running application on `app.onechapter.am` is the reader and library. Its access model will evolve in two stages:

1. **Stage 1 (Operator & Invite-Only):** While the app is being deployed and tested by the operator and a small circle of invited readers, unauthenticated visitors must not have access to the reader or library.
2. **Stage 2 (Public Reader with Unified Accounts):** Anyone visiting `onechapter.am` or starting `@one_chapter_bot` can sign in (starting with **Login with Telegram**, extensible to Email/OAuth later per [ADR 0006](0006-unified-identity-and-access-control.md)) and read at their own daily pace.

Regardless of stage, server administration (such as the Telegram bot token) belongs in server environment configuration or staff-only Django Admin—never on a public web form.

## Decision

Split the public marketing site and the web application across two hostnames:

| Hostname | Purpose | Access Policy |
| --- | --- | --- |
| `onechapter.am` | Public static landing page on Cloudflare (Workers static assets / Pages) explaining the practice and linking to `@one_chapter_bot` and `app.onechapter.am` | Public (anyone) |
| `app.onechapter.am` | Django web reader, library, and reflection journal proxied to the EC2 app server | **Stage 1:** Cloudflare Access (allowlisted emails) + Login with Telegram.<br/>**Stage 2:** Public HTTPS edge with Django authentication (Login with Telegram, plus future Email/OAuth). |

Additional boundary rules:

- **Remove bot token mutation from the reader UI:** The Telegram bot token (`TELEGRAM_BOT_TOKEN`) is configured via environment variables on the server, not editable from `/settings`. The `/settings` page is strictly for the signed-in reader's personal preferences (timezone, daily delivery time, connected channels).
- **Minimal `/health` endpoint:** `/health` returns only liveness (`{"ok": true}`) without leaking internal bot metadata.

## Consequences

- `onechapter.am` can ship immediately as a fast, zero-cost static site on Cloudflare before or independent of the EC2 deployment, and stays online even if the app server is restarting.
- In Stage 1, Cloudflare Access provides a zero-trust perimeter around `app.onechapter.am` before opening registration to the public.
- Moving from Stage 1 (invite-only) to Stage 2 (public) requires only removing the Cloudflare Access gate on `app.onechapter.am` and switching `ONECHAPTER_ACCESS_MODE=public` ([ADR 0006](0006-unified-identity-and-access-control.md))—the hostname split (`onechapter.am` vs. `app.onechapter.am`) and URLs remain unchanged.

## Alternatives considered

- **Serve both the landing page and the reader from a single hostname.** Mixes public static caching rules with private, per-reader dynamic responses and makes Cloudflare Access gating in Stage 1 require path-level bypass rules.
- **Keep `/settings` as an unauthenticated operator console.** Unsafe once more than one person can reach `app.onechapter.am`; operator secrets belong in environment variables and staff-only admin views.
