# ADR 0008: Operator privilege isolation and explicit account blocking

- Status: Accepted (implemented in PR #9: `src/onechapter/config.py`, `src/onechapter/reader/admin.py`, `src/onechapter/reader/middleware.py`, `src/onechapter/reader/migrations/0002_reader_is_blocked.py`, `src/onechapter/library.py`, `src/onechapter/bot/handlers.py`, `src/onechapter/reader/views.py`)
- Date: 2026-11-18

## Context

[ADR 0006](0006-unified-identity-and-access-control.md) introduced `ONECHAPTER_ACCESS_MODE=invite|public`, a single allowlist (`ONECHAPTER_ALLOWED_TELEGRAM_IDS`), and `Reader.is_allowed` / `Reader.is_operator`. Two security gaps emerged when preparing to switch `app.onechapter.am` from Stage 1 (invite-only) to Stage 2 (public sign-up):

1. **Operator privilege escalation risk:** Treating general allowlisted users or unconfigured public-mode users as eligible for Django Admin (`is_staff` / `is_superuser`) or exposing bot-token controls on `/settings` would risk granting non-operators administrative access if `ONECHAPTER_ACCESS_MODE=public` is enabled without explicit operator configuration.
2. **Account revocation in `public` mode:** Using `Reader.is_allowed = False` alone cannot reliably ban a reader when `ONECHAPTER_ACCESS_MODE=public`, because public mode auto-allows sign-ins unless an explicit block state takes precedence.

## Decision

1. **Dedicated Operator Allowlist (`ONECHAPTER_OPERATOR_TELEGRAM_IDS`) & Fail-Closed Admin Gate:**
   - Separate operator authorization into `ONECHAPTER_OPERATOR_TELEGRAM_IDS` (distinct from `ONECHAPTER_ALLOWED_TELEGRAM_IDS`).
   - Enforce `is_admin_user_allowed(user)` in both `admin.site.has_permission` and `BootstrapMiddleware` (returning `403 Operator access only.` for authenticated non-operators hitting `/admin*`):
     - Requires `user.is_active`, `user.is_staff`, and `user.is_superuser`.
     - Denies access if the linked `Reader` is blocked (`is_blocked=True`), not allowed (`is_allowed=False`), or not an operator (`is_operator=False`).
     - When `ONECHAPTER_OPERATOR_TELEGRAM_IDS` is configured, requires a matching `ChannelIdentity(channel="telegram", external_id__in=op_ids)`.
     - **Fails closed in `public` mode:** If `ONECHAPTER_OPERATOR_TELEGRAM_IDS` is empty and the app is not in unconfigured local single-user dev (`is_local_single_user_dev()`), Django Admin access is denied to everyone.
   - Non-operator readers never see server bot-token controls in `/settings`.

2. **Explicit `Reader.is_blocked` Field (Migration `0002_reader_is_blocked`):**
   - Add `Reader.is_blocked = BooleanField(default=False)` alongside `is_allowed`.
   - In Django Admin (`ReaderAdmin.save_model`), blocking a reader automatically sets `is_allowed = False`, and revoking `is_allowed` sets `is_blocked = True` so operators cannot accidentally leave a revoked user able to re-enter in `public` mode.
   - Enforce `is_blocked` and `user.is_active` checks uniformly across web views (`_require_reader_or_redirect`), magic-link / Telegram login endpoints, Django Admin, and Telegram bot handlers (`_require_allowed_reader`).

3. **PostgreSQL 17 CI Verification:**
   - Run the full `pytest` suite on every push to `main` and pull request via `.github/workflows/ci.yml` against a real `postgres:17-alpine` service container, covering operator isolation, fail-closed public admin checks, and account blocking.

## Consequences

- Switching `ONECHAPTER_ACCESS_MODE` from `invite` to `public` never accidentally opens `/admin/` or `/settings` operator controls, even if `ONECHAPTER_OPERATOR_TELEGRAM_IDS` is omitted.
- Operators can permanently block abusive or revoked accounts in both `invite` and `public` modes without changing the global access mode.
- Local single-developer mode (`DJANGO_DEBUG=1` with no access env vars set) remains zero-friction while production (`DJANGO_DEBUG=0`) stays strict by default.

## Alternatives considered

- **Reuse `ONECHAPTER_ALLOWED_TELEGRAM_IDS` for both invited readers and operators.** Couples reader invitations with admin privileges, violating least privilege.
- **Rely solely on `User.is_active = False` for blocking.** Does not cover Telegram-only readers who have never established a Django web session or `User` record, whereas `Reader.is_blocked` gates both Web and Telegram uniformly.
