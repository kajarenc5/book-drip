# ADR 0001: Cloudflare is the edge, name.am stays the registrar

- Status: Proposed
- Date: 2026-11-09

## Context

`onechapter.am` is registered at name.am. The app is not yet deployed. The AWS project can create regional resources only in the selected Region (`eu-north-1`), and the project is on the Free plan with \$100.00 USD of remaining credits through `2027-05-06`.

The site needs a public domain, HTTPS, DDoS protection, and a clean edge in front of the app server without paying for an AWS Application Load Balancer or Route 53 hosted zone. Cloudflare Registrar does not support `.am` domain transfers, so registration must remain at name.am.

Additionally, Django's built-in `django.contrib.staticfiles` only serves `/static/` when `DEBUG = True`. In production (`DEBUG = False`), requests to `/static/` will return `404` unless static files are collected and served by either a production static middleware or the reverse proxy.

## Decision

Leave the domain registration at name.am. Add `onechapter.am` to Cloudflare as a full DNS zone and update the nameservers at name.am to the pair assigned by Cloudflare. If DNSSEC is enabled at the registrar, disable it before changing nameservers and re-enable DNSSEC at Cloudflare once the zone is Active.

Cloudflare is the single public edge:

- **Proxied records (orange cloud)** for `onechapter.am` and `app.onechapter.am`.
- **SSL/TLS mode:** `Full (strict)`.
- **Origin connection:**
  - **Primary path:** Run **Caddy** (or Nginx) on the EC2 instance listening on port `443` with a Cloudflare Origin CA certificate (and Authenticated Origin Pulls / Security Group locked to [Cloudflare's published IPv4/IPv6 ranges](https://www.cloudflare.com/ips/)), reverse-proxying to the app container at `127.0.0.1:43147`.
  - **Zero-inbound-port alternative:** Run `cloudflared` (Cloudflare Tunnel) as a lightweight container in `docker-compose.yml` forwarding `app.onechapter.am` to `http://app:43147`, requiring zero inbound ports on the EC2 Security Group.
- **Static assets (`WhiteNoise`):** Add `whitenoise.middleware.WhiteNoiseMiddleware` to Django with `CompressedManifestStaticFilesStorage` and run `python manage.py collectstatic --noinput` during the Docker image build.
- **Caching:** Cache `/static/*` at Cloudflare (safe for long TTLs because WhiteNoise fingerprints asset filenames). Never cache HTML responses on `app.onechapter.am`, because "today's chapter" unlocks at local midnight per reader.
- **Canonical redirect:** Redirect `www.onechapter.am` to `https://onechapter.am`.

Do not create a Route 53 hosted zone, an AWS Application Load Balancer, or a CloudFront distribution.

## Consequences

- Registrar management and renewal stay at name.am; all DNS, TLS termination, and edge caching live in Cloudflare at \$0/month.
- The EC2 instance is never exposed directly to the public internet; administration uses AWS Systems Manager Session Manager rather than SSH (port 22 remains closed).
- Production Django settings must enforce:
  - `DEBUG = False`
  - Strong `DJANGO_SECRET_KEY` from environment
  - `ALLOWED_HOSTS = ["app.onechapter.am"]`
  - `CSRF_TRUSTED_ORIGINS = ["https://app.onechapter.am"]`
  - `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`
  - `SESSION_COOKIE_SECURE = True` and `CSRF_COOKIE_SECURE = True`
- Static CSS/JS assets work reliably with `DEBUG = False` and are served compressed with content-hashed URLs.

## Alternatives considered

- **Route 53 plus CloudFront or ALB.** Adds monthly hosted-zone and load-balancer charges (an ALB alone costs ~\$18+/month in `eu-north-1`, which would consume the project's \$100 credits prematurely) and duplicates Cloudflare's CDN and TLS features.
- **Transfer `onechapter.am` to Cloudflare Registrar.** Cloudflare Registrar does not support the `.am` TLD.
- **DNS-only (grey cloud) with Let's Encrypt on EC2.** Exposes the instance IP directly to public traffic and gives up Cloudflare Access, edge caching for `/static/`, and DDoS mitigation.
