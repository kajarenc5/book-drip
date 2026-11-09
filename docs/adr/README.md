# Architecture Decision Records

Proposed decisions for running One Chapter at `onechapter.am`. Nothing here is implemented. Status stays **Proposed** until review.

| ADR | Decision |
| --- | --- |
| [0001](0001-cloudflare-edge.md) | name.am keeps the registration. Cloudflare is DNS and the HTTPS edge. |
| [0002](0002-public-landing-and-private-app.md) | The apex is a public static page. The reader lives on `app.onechapter.am` behind Cloudflare Access. |
| [0003](0003-one-app-server.md) | One server in `eu-north-1` runs the website and the Telegram bot. |
| [0004](0004-aurora-postgresql.md) | Reading state moves from SQLite to Aurora PostgreSQL serverless. |
| [0005](0005-s3-book-originals.md) | Uploaded book files are stored in S3 and can be ingested again. |

Diagrams are in [../architecture/README.md](../architecture/README.md).
