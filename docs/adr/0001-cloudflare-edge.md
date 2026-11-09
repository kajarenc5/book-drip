# ADR 0001: Cloudflare is the edge, name.am stays the registrar

- Status: Proposed
- Date: 2026-11-09

## Context

`onechapter.am` is registered at name.am. The app is not deployed. The AWS project can create regional resources only in `eu-north-1`, and the account is on the Free plan with $100 of credits through 2027-05-06.

The site needs a public name, HTTPS, and a place to attach the app later. Cloudflare Registrar does not sell `.am`, so the name cannot move there.

## Decision

Leave the registration at name.am. Add the domain to Cloudflare as a full DNS setup and change the nameservers at name.am to the pair Cloudflare assigns. If DNSSEC is enabled at the registrar, turn it off before the nameserver change and enable it again at Cloudflare after the zone is Active.

Cloudflare is the only public edge:

- Proxied records (orange cloud) for the website.
- SSL/TLS mode **Full (strict)**.
- An origin certificate on the app server. Cloudflare's proxy does not connect to the app's port 43147, so a reverse proxy on the server listens on 443 and forwards to the app.
- Cache `/static/` only. Reader HTML stays uncached, because "today" changes at local midnight.
- `www` redirects to the apex.

Do not create a Route 53 hosted zone or a CloudFront distribution for this site. Do not put a second CDN in front of Cloudflare.

## Consequences

- The registrar change is two nameservers. Renewal stays at name.am.
- The app server is not on the public internet except for port 443 from [Cloudflare's IP ranges](https://www.cloudflare.com/ips/). Administration is AWS Systems Manager, not SSH.
- Django must trust the proxy: `DEBUG` off, a real `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS` limited to the app hostname, `CSRF_TRUSTED_ORIGINS` set to `https://app.onechapter.am`, and `SECURE_PROXY_SSL_HEADER` set so Django sees HTTPS.
- An empty Cloudflare zone is acceptable until the app exists. Visitors get a Cloudflare response, not the reader.

## Alternatives considered

- **Route 53 plus CloudFront.** A second DNS host and a second CDN, with extra TLS and cache rules, and no benefit once Cloudflare is authoritative.
- **Transfer the domain to Cloudflare Registrar.** `.am` is not a registrar TLD Cloudflare sells.
- **DNS-only (grey cloud) and TLS on the instance with Let's Encrypt.** Works, and gives up the edge cache, DDoS protection, and Access in front of the reader.
