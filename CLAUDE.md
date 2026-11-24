<!-- BEGIN AWS Agent Toolkit rules -->
# AWS Guidance for the new AWS experience

This user has signed up for the new AWS experience. This experience lets you sign into AWS using a social provider and requires the following additional context.

Where this guidance conflicts with the project's own instructions, the project's instructions take precedence.

## Context

### Terminology:

- Say "project" instead of "account" — a project contains an AWS account and settings for sharing with other collaborators
- Say "team member" instead of "IAM user" — users are invited by email, not created or federated in IAM
- Say "AWS Settings" when referring to management tasks at [settings.aws.com](https://settings.aws.com/) (project management, billing, team members, spend limits). Users view their actual AWS resources in the AWS Management Console.
- Say "selected Region" when referring to the user's Region — not "home Region"
- The user has a managed IAM experience. This includes a managed service control policies (SCP) and resource control policies (RCP) that govern the use of AWS. They will still need to use IAM to create policies to let services work with each other. If there are questions about the SCPs or RCPs, go to the documentation at https://docs.aws.amazon.com/accounts/latest/reference/scps-and-rcps-for-projects.html

### Constraints:

- All projects share a single AWS Region determined by the user's contact address. Resources cannot be created in other Regions
- When developing:
  - MUST create all Regional resources in the project's assigned Region
  - You CAN create AWS WAF and Cloudwatch Logs resources in us-east-1 when there are global resources (like a global WAF instance) that require a connection to dependencies in us-east-1. You should not use these for any other reason, because resources in the selected Region will provide lower cost (due to no cross-Region traffic), increased availability (due to no cross-Region traffic), and easier manageability (due to not needing to look in another Region). When you need to do an inventory of resources, you need to look in both the selected Region and us-east-1 for Cloudwatch Logs or WAF resources.
  - MUST NOT attempt to create Lambda, API Gateway, or other Regional resources in any other Region
  - MUST direct users to confirm their Region in AWS Settings > View all projects > Overview > Additional Info > Region. If the user cannot confirm their Region, check in ~/.aws/config
  - MUST NOT use Lambda@Edge — excluded from both Lambda and CloudFront
  - MUST NOT use CloudFormation StackSets — no multi-account or multi-Region deployments
  - MUST NOT attempt cross-Region actions — no cross-Region replication for DynamoDB/S3/RDS, no multi-Region KMS keys
  - MUST NOT use Route 53 cross-Region routing — geolocation, latency-based, and failover routing policies are not available
  - CloudFront is a global service and its actions ARE allowed in `us-east-1`. A user can create a CloudFront distribution pointing to their project-region Lambda function URL or API Gateway. However, Lambda and API Gateway themselves MUST NOT be created in `us-east-1` — they must be in the project Region.
  - Reduced availability in `eu-north-1` specifically: Amazon Rekognition, Amazon Textract, Amazon Personalize, AWS App Runner are not available in that Region.
- IAM permissions for human access are managed by AWS. Don't assign roles to team members unless absolutely necessary
- The user may have a spend limit if they are on the paid plan. The limit that pauses their project if it's exceeded. If resources suddenly become inaccessible, ask if they have a spend limit configured. Only project owners can modify a spend limit.
- When developing:
  - MUST ask about spend limit status if the user reports sudden "Access Denied" errors on operations that previously worked
  - MUST direct users to check spend status in AWS Settings > Billing
  - MUST check if a user has upgraded their account to the paid plan
  - MUST ask the user if they want to clean up the successfully created resources or keep them to reduce cost
- The user sets up billing, creates spend limits, and retrieves and pays invoices in AWS Settings. The user creates budgets and optimizes their costs in the AWS Billing and Cost Management console
- Not all AWS services are available. If a service isn't working, do the following:
  1. Run the command `aws freetier get-account-plan-state`
  2. If accountPlanType": "FREE", check the [Free Tier supported services list](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html#supported-services-free-tier) next,
  3. If accountPlanType": "PAID", check the [Paid Tier supported services list](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html#supported-services-paid-plan).
  4. If neither list shows the service, check the [Not supported for this experience list](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html#unsupported-services). The user will need to activate advanced features to access this service.
- Users can activate advanced AWS services and capabilities for their account.
- For any AWS task or question — building, configuring, migrating, debugging, or operating a service, writing SDK code, authoring CDK/CloudFormation, or answering how a service works — first call `aws___search_documentation` with topic `agent_skills` and load the matching skill with `aws___retrieve_skill`. Do this even when you believe you know the answer: skills carry current service behavior that your training may not. Prefer the skill's guidance over general knowledge. Skip it only for work that does not involve AWS.

### Help level

- Current saved setting: `help_level: MEDIUM`
- help_level (required): LOW, MEDIUM, or HIGH. While a user is building, you MUST ask the user: "How much guidance would you like from me? Low (I only flag security risks), medium (I ask a couple of clarifying questions if something seems off), or high (I explain what I'm doing, suggest alternatives, and flag best practices)."

You CAN update this rule file to save a user's help_level.

Constraints for each level:

**LOW:**

- MUST follow all constraints in this context file
- MUST execute the user’s request without modification
- MUST NOT ask clarifying questions unless the action would create a security vulnerability
- MUST NOT suggest alternatives or improvements

**MEDIUM:**

- MUST execute the user's request
- MAY ask up to two clarifying questions per task if the request has an ambiguity or a potential issue
- MUST NOT repeat a question or suggestion the user has already dismissed
- MUST NOT explain trade-offs or alternatives unless the user asks

**HIGH:**

- MUST explain what each step does and why before executing it
- MUST suggest alternatives when a better approach exists
- MUST flag best practices and explain trade-offs
- MUST still execute the user's choice if they disagree with a suggestion
<!-- END AWS Agent Toolkit rules -->

# Project & Environment Context (`book-drip` / One Chapter)

- **Workspace**: `/Users/kajarenc7/work/book-drip` on the local Mac (One Chapter — a Python slow-reading companion app on Telegram and the web, deployed in `eu-north-1` behind Cloudflare at `https://onechapter.am` and `https://app.onechapter.am`).
- **Agent Rule Files**: `.cursor/rules/aws-agent-toolkit.mdc`, `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` contain project and AWS guidance. Read them before any AWS work.

## Git & GitHub (Source of Truth)

- `origin` is `git@github.com:kajarenc5/book-drip.git` (public repo, default branch `main`). `gh` is logged in as `kajarenc5` over SSH.
- Push and open pull requests against `origin`. Local `main` tracks `origin/main`.
- A second remote named `cursor` points at `https://origin.cursor.com/git/kajarenc/book-drip`. It was behind GitHub, and this machine has no login for that host. **Do not push to `cursor` and do not treat it as the project remote.**
- Cloud agents should use the GitHub repo. The saved Cursor environment (`uv` install and the `onechapter` start command) is registered for `origin.cursor.com/git/kajarenc/book-drip`, not for GitHub; a session opened from GitHub gets the code and may need to install dependencies (`uv sync`) on its first run.

## AWS Setup & Local Profiles

- **AWS Experience**: New AWS experience, project `989240881256`, selected Region `eu-north-1`.
- **Plan**: `FREE` (`ACTIVE`), with \$100.00 USD of remaining credits through `2027-05-06`.
- **Region Constraint**: Create regional resources **only** in `eu-north-1`.
- **Local Config (`~/.aws/config`)**: There is no `[default]` AWS profile and no `~/.aws/credentials` file. Two profiles exist in `~/.aws/config`:
  1. `book-drip`: `aws login` session (`arn:aws:sts::989240881256:assumed-role/AccountFullAccessRole/...`) for the managed role `arn:aws:iam::989240881256:role/managed/AccountFullAccessRole` in `eu-north-1`. The CLI refreshes it for up to 12 hours, after which someone must run `aws login` again. **Do not replace this with long-term access keys unless the user explicitly asks (already declined).**
  2. `book-drip-local`: Assumes `arn:aws:iam::989240881256:role/book-drip-local` from `source_profile = book-drip`, Region `eu-north-1`, `role_session_name = book-drip-mac`, `duration_seconds = 3600` (1 hour, auto-renewed from the login while valid). That role has `AdministratorAccess` and trusts only the managed login role. **Always use `--profile book-drip-local` for AWS work on this Mac.**

## Cloud Agent & IAM Constraints

- Cloud agents can edit the repo, but **cannot call or deploy to AWS**. Deployment to AWS stays on this Mac.
- The project's policy explicitly denies creating an OIDC provider, IAM Roles Anywhere, and a SAML provider.
- The Cursor-managed role path requires a team External ID, and this Cursor account is on an individual Pro plan with no team.
  - **Do not** create a Cursor team.
  - **Do not** create a cloud IAM role that trusts Cursor's role assumer until the user has a team and an External ID.

## Workflow Reminders Before AWS Work

- The user's saved guidance preference is `help_level: MEDIUM` (ask up to two clarifying questions if the request has an ambiguity or potential issue, and execute directly without unnecessary explanations unless asked).
- Ask before leaving newly created AWS resources in the project so the user can choose to delete them or keep them.

## Implemented Architecture & Key Decisions (`docs/architecture/` & `docs/adr/`, ADR 0001–0011)

- **Edge, Landing Site & Origin TLS ([ADR 0001](docs/adr/0001-cloudflare-edge.md), [ADR 0002](docs/adr/0002-public-landing-and-private-app.md))**:
  - `onechapter.am` stays registered at `name.am` with Cloudflare as authoritative DNS and HTTPS edge (`Full (strict)`).
  - `onechapter.am` is deployed as a public static landing page on Cloudflare Workers (`site/`, configured in `site/wrangler.jsonc`); `app.onechapter.am` is the Django web reader behind Caddy (`infra/docker/Caddyfile`) with Cloudflare Origin CA TLS installed via `infra/aws/install-origin-cert.sh` (private key never leaves EC2).
  - Django serves static assets in production (`DEBUG=False`) via `WhiteNoise` (`CompressedManifestStaticFilesStorage`) with immutable `/static/*` caching at Caddy/Cloudflare, plus HSTS, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, and `X-Frame-Options: DENY`.
- **Compute, Database & Release Flow ([ADR 0003](docs/adr/0003-one-app-server.md), [ADR 0004](docs/adr/0004-postgresql-and-django-orm.md))**:
  - **One Graviton EC2 instance (`t4g.small` default)** in `eu-north-1` (`infra/aws/template.yaml`, `infra/aws/deploy.sh`, `infra/aws/release.sh`) runs Docker Compose (`proxy`, `onechapter`, `db`, `db-backup`) within a ~\$9–\$12/month budget so the \$100.00 Free Plan credit lasts through `2027-05-06`. Releases run via SSM Session Manager with an automatic pre-deploy S3 backup and `/health` check.
  - **PostgreSQL 17 runs in a Docker container on the same EC2 instance** (internal Docker network only, EBS `gp3` volume) with automated daily and pre-deploy backups (`infra/scripts/db-backup.sh` / `db-restore.sh`) to S3 (`backups/postgres/`). **Do not use Aurora Serverless v2** (1-minute delivery ticks prevent `0 ACU` auto-pause and burn ~\$45+/month, plus 15s cold starts).
  - **PostgreSQL 17 + Django 5.2 ORM everywhere** (local dev, CI `.github/workflows/ci.yml` with `postgres:17-alpine`, and production; SQLite is not used). Locally (when `DATABASE_URL` is unset), `src/onechapter/local_pg.py` auto-starts Homebrew `postgresql@17` in `data/postgres/` (`127.0.0.1:54329`, managed via `uv run onechapter db {start,stop,status,psql,reset}`). Blocking CPU/IO work (`ingest_file`, S3 calls) runs off the asyncio event loop (`asyncio.to_thread` / `sync_to_async`).
- **S3 Layout, Upload Hardening & Reprocessing ([ADR 0005](docs/adr/0005-s3-book-originals.md), [ADR 0009](docs/adr/0009-upload-security-hardening-and-atomic-parse-before-save.md))**:
  - Single private S3 Standard bucket in `eu-north-1` (`onechapter-989240881256-eu-north-1`) with prefixes: `originals/{sha256}/{filename}` (immutable uploaded books), `backups/postgres/` (30-day lifecycle), and `media/reflections/{reader_id}/{uuid}.{ext}` (future voice reflections).
  - **Atomic parse-before-save & upload security:** Uploads are capped at 25 MiB web (26 MB in Caddy) and 20 MiB Telegram, rate-limited (5/reader and 10/IP per 10 min), validated against ZIP/MOBI archive bombs, path traversal, nested/encrypted archives, disguised binaries, and XXE/Billion Laughs/external DTD/XInclude attacks, and fully extracted/split into non-empty chapters **before** storing to S3 or inserting DB rows (with S3 rollback on DB failure).
- **Identity, Operator Isolation & Account Blocking ([ADR 0006](docs/adr/0006-unified-identity-and-access-control.md), [ADR 0008](docs/adr/0008-operator-privilege-isolation-and-account-blocking.md))**:
  - One shared Telegram bot (`@one_chapter_bot`) for all readers (use `@one_chapter_dev_bot` locally).
  - Canonical `Reader` + `ChannelIdentity` (`telegram`, `signal`, `whatsapp`, `email`) unifies Web and Telegram via Login with Telegram widget + `/login` magic link.
  - Access is controlled by `ONECHAPTER_ACCESS_MODE=invite|public`. Operator admin access is isolated to `ONECHAPTER_OPERATOR_TELEGRAM_IDS` and **fails closed** on `/admin` in `public` mode if empty. `Reader.is_blocked` (migration `0002`) explicitly bans accounts even in `public` mode across Web, Telegram, and Admin.
  - Shared library hides `Book.uploaded_by` from readers (internal audit only); regular users cannot overwrite, re-split, or delete existing books (operator-only `reprocess`).
- **Reflections, Telegram Reliability & Book Splitters ([ADR 0007](docs/adr/0007-extensible-reflections-and-media.md), [ADR 0010](docs/adr/0010-telegram-delivery-reliability-and-public-onboarding.md), [ADR 0011](docs/adr/0011-book-splitter-profiles-and-multilingual-extraction.md))**:
  - Reprocess-safe `Chapter.chapter_key` + `Reflection` snapshots (web + Telegram plain-text notes, capped at 10,000 chars; audio/transcript fields ready).
  - Daily delivery uses bulk queries (no N+1), auto-disables `ChannelIdentity.is_delivery_enabled` on permanent Telegram `Forbidden`/`BadRequest` errors, handles `RetryAfter` with backoff and partial chunk resumption, keeps `drop_pending_updates=False`, supports `/stop`/`/unsubscribe` and web delivery toggles, and auto-detects browser timezone for new web logins only.
  - Dedicated splitter profiles: `tarasov` (`tehnologiya-zhizni`, `ru`), `gosudar` (Russian *Государь*, `gosudar`, `ru`, coexisting with seeded English `the-prince`), `art_of_war` (Russian *Искусство войны* by Sun Tzu, `iskusstvo-voyny`, `ru`, 13 chapters), and `generic`, with paragraph-preserving HTML/FB2 extraction, ASCII transliterated slugs (`slugify_ascii`), and automatic Cyrillic/language detection.


