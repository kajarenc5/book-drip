# ADR 0010: Reliable Telegram delivery, rate-limit handling, and public onboarding polish

- Status: Accepted (implemented in PR #5, PR #8, and PR #13: `src/onechapter/bot/handlers.py`, `src/onechapter/bot/runtime.py`, `src/onechapter/reader/views.py`, `src/onechapter/project/settings.py`, `infra/docker/Caddyfile`, `tests/test_runtime.py`, `tests/test_web.py`)
- Date: 2026-11-18

## Context

As One Chapter moved toward hosting multiple readers on `@one_chapter_bot` and `app.onechapter.am`, several operational and onboarding edge cases needed explicit architectural rules:

1. **N+1 queries on every 1-minute tick:** Checking due deliveries per plan individually issued repeated chapter and delivery queries on each scheduler cycle.
2. **Unreachable chats & Telegram rate limits:** If a reader blocked `@one_chapter_bot` or deactivated their account, retrying every minute caused noisy error loops. Conversely, if Telegram returned `RetryAfter` mid-chapter (a long chapter split into multiple HTML chunks), naive retries either dropped the chapter or re-sent already-delivered chunks.
3. **Deploy restarts dropping user commands:** Starting polling with `drop_pending_updates=True` silently discarded commands sent during brief container rebuilds.
4. **Onboarding friction & security defaults:** New web readers defaulted to `Asia/Yerevan` even when signing in from other timezones, needed a self-service way to pause/mute Telegram messages without deleting their web reading plan, and needed reflection length limits and hardened HTTP security headers.

## Decision

1. **Bulk Due-Delivery Collection (`_collect_due_deliveries`):**
   - Collect active plans, evaluate due states, and fetch existing `Delivery` rows and needed `Chapter` rows (today + tomorrow's title preview) in bulk queries rather than per-plan N+1 queries.

2. **Graceful Telegram Error & Rate-Limit Handling (`send_due_chapters`):**
   - **Permanent chat errors:** Catch `Forbidden` and permanent `BadRequest` errors (`chat not found`, `user is deactivated`, `bot was blocked`, etc.) and automatically set `ChannelIdentity.is_delivery_enabled = False` (`reason="telegram_unreachable"`), stopping retry loops while preserving the reader's web reading plan.
   - **Transient `RetryAfter` backoff & chunk resumption:** Record `_retry_after_until_ts` to defer remaining deliveries until Telegram's backoff window expires, and track per-`(plan_id, local_date)` chunk progress in `_partial_chunk_progress` so resumed delivery continues from the next unsent chunk without duplicating earlier chunks.
   - **Polling continuity & timeouts:** Configure `Application.builder()` with 15-second connect/read/write/`get_updates` timeouts and call `start_polling(drop_pending_updates=False)` so commands sent during quick deploys are processed on startup.

3. **User-Controlled Delivery Opt-Out & Onboarding Polish:**
   - Support `/stop` and `/unsubscribe` in Telegram (and a toggle on web `/settings`) to turn off daily Telegram messages independently of plan pause; `/start` and `/resume` re-enable Telegram delivery.
   - Capture the browser's IANA timezone (`onechapter_tz` cookie / `tz` parameter) on Web Telegram login **for newly created readers only** so existing readers' saved preferences are never overwritten; validate all timezone updates against `zoneinfo.ZoneInfo`.
   - Allow plain-text messages in `@one_chapter_bot` to save chapter reflections (`source_channel="telegram"`), and cap reflection length at `ONECHAPTER_MAX_REFLECTION_CHARS = 10,000` on both Web and Telegram.
   - Enforce HTTP security headers in both Django (`settings.py`) and Caddy (`Caddyfile`): `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, and `X-Frame-Options: DENY`.

## Consequences

- Daily delivery scales cleanly across many readers on a single process without N+1 database queries or infinite retries against blocked chats.
- Multi-chunk chapters survive Telegram `RetryAfter` throttling without duplicate messages.
- Readers can mute Telegram notifications while continuing to read on `app.onechapter.am`, and new web sign-ups get their local timezone automatically.

## Alternatives considered

- **Per-plan DB queries inside the scheduler loop.** Simple initially, but degrades linearly with reader count every 60 seconds.
- **Ignore `Forbidden` / `RetryAfter` and let the next 1-minute tick retry everything.** Risks Telegram flood bans and sends duplicate chunks when a long chapter fails partway through.
- **Use `drop_pending_updates=True` on boot.** Causes `/start`, `/login`, or `/today` sent during a 10-second release restart to vanish with no reply.
