# ADR 0006: Unified multi-channel reader identity and shared library access control

- Status: Proposed
- Date: 2026-11-09

## Context

In the initial prototype:
- Web visitors always resolved to a single hardcoded `Local reader` row (`telegram_id IS NULL`), while Telegram users resolved to separate `readers` rows (`telegram_id = <id>`). Even for a single user, reading plans, delivery times, and reflections on the web were disconnected from Telegram.
- The Telegram bot had no access control: anyone could read any book, upload files that overwrote existing books, and see the entire catalog.
- Identity was coupled directly to `telegram_id` on the `readers` table, making it harder to add web login or additional messaging channels later.

The target product requirements are:
1. **One shared Telegram bot (`@one_chapter_bot`)** for all readers.
2. **Staged onboarding:** Start as **invite-only** (operator + invited friends/readers), with a clean configuration switch to open **publicly** later so anyone visiting `onechapter.am` or starting `@one_chapter_bot` can begin reading.
3. **Consistent identity across Web and Telegram (`Login with Telegram` first),** designed so a reader can later attach additional login methods (Email/Google OAuth) and messaging channels (**Signal**, **WhatsApp**) to the same account.
4. **Shared, privacy-preserving library:** Any authorized reader can upload a book and any reader can read books uploaded by others, **without ever exposing who uploaded a book**. Regular users can add books and read books, but **cannot overwrite/delete existing books** or modify operator settings.

## Decision

### 1. Canonical `Reader` Account + `ChannelIdentity` Model

Represent each person as a single canonical `Reader` profile (one-to-one with or extending Django's `User` model) and decouple communication/login channels into a separate `ChannelIdentity` table:

- **`Reader` (User profile):**
  - `id` (PK), `user_id` (Django auth `User`), `display_name`, `timezone` (default `Asia/Yerevan`), `delivery_hhmm` (default `08:00`), `is_allowed` (boolean for invite-only gating), `is_operator` (`is_staff`), `created_at`.
- **`ChannelIdentity`:**
  - `id` (PK), `reader_id` (FK -> `Reader`, `ON DELETE CASCADE`)
  - `channel` (`'telegram' | 'signal' | 'whatsapp' | 'email'`)
  - `external_id` (e.g., Telegram numeric user ID, E.164 phone/UUID for Signal/WhatsApp, normalized email)
  - `handle` (optional username, e.g., `@username`, for display only)
  - `is_delivery_enabled` (boolean), `verified_at`, `metadata` (`JSONField`)
  - **`UNIQUE (channel, external_id)`**

All reading plans (`Plan`), deliveries (`Delivery`), and notes/voice memos (`Reflection`) reference `reader_id`—never a channel-specific ID.

### 2. Authentication: Login with Telegram Now, Multi-Auth Later

- **Phase 1 (Telegram-first):**
  - On `app.onechapter.am`, readers sign in using the **Telegram Login Widget** (verified on the backend via `HMAC-SHA256` using `TELEGRAM_BOT_TOKEN`) or via a one-time magic login link requested with `/login` in `@one_chapter_bot`.
  - Logging in looks up `(channel='telegram', external_id=<tg_id>)` in `ChannelIdentity`, resolves the canonical `Reader`, and establishes a standard Django session.
  - Changing timezone, delivery time, or reading state on the web immediately updates the exact same `Reader` and `Plan` used by `@one_chapter_bot`.
- **Phase 2 (Email/OAuth & Additional Messaging Channels):**
  - Adding Email Magic Link or Google OAuth attaches or resolves the same `Reader` and `User` record.
  - Adding Signal or WhatsApp delivery only requires inserting a `ChannelIdentity(channel='signal', ...)` row for the reader and implementing a channel sender adapter for the daily delivery tick.

### 3. Invite-Only -> Public Access Control (`ONECHAPTER_ACCESS_MODE`)

- Controlled by setting `ONECHAPTER_ACCESS_MODE = "invite" | "public"` (default `"invite"` initially):
  - **`invite` mode:** New users interacting with `@one_chapter_bot` or `app.onechapter.am` must either have their Telegram ID pre-allowlisted by an operator or present a valid invite code (`/start inv_<code>`). Uninvited users receive a friendly message explaining that One Chapter is currently in private invite-only beta.
  - **`public` mode:** Any user who starts `@one_chapter_bot` or signs in on `app.onechapter.am` is automatically marked `is_allowed = True` and can start reading immediately.

### 4. Shared Library Permissions & Anonymous Attribution

| Action | Regular Reader (`is_allowed=True`) | Operator (`is_staff=True`) |
| --- | --- | --- |
| Browse `/books` and read any book in the catalog | Allowed (sees book title, author, chapter count only; **never sees who uploaded a book**) | Allowed |
| Upload a new book (`.mobi`, `.epub`, `.fb2`, `.txt`, `.html`) | Allowed (adds to catalog if `sha256` is new; links existing book if already uploaded) | Allowed |
| Overwrite, re-split (`reprocess`), edit metadata, or delete an existing book | **Denied** | Allowed |
| View `Book.uploaded_by` (internal audit field) | **Denied** (never rendered in templates, bot messages, or public responses) | Allowed (Django Admin only) |
| Change bot token or global server settings | **Denied** | Allowed (env / Admin) |

## Consequences

- Web and Telegram are always 100% in sync because they share the same `Reader` and `Plan` rows.
- Books uploaded by any reader enrich the shared library without exposing the uploader's identity to other readers, and no reader can accidentally or maliciously overwrite an existing book.
- Extending One Chapter to Signal, WhatsApp, or Email login later requires zero changes to `Reader`, `Plan`, `Chapter`, or `Reflection` tables.

## Alternatives considered

- **Keep `telegram_id` directly on `readers` and maintain a separate local web reader.** Causes split-brain state between web and Telegram and requires schema migrations later when adding Signal, WhatsApp, or email-only web users.
- **Scope uploaded books privately to the uploader only.** Rejects the desired shared-library experience where a book uploaded once can be read by other readers in the community.
