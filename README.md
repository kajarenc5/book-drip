# One Chapter

A slow-reading companion for books made of short, dense chapters.

It does two things, and refuses to do a third:

1. Split a book into daily units — for Vladimir Tarasov’s *Технология жизни. Книга для героев*, that means the titled essays (“Человек, который кормил обезьян”, “Горизонтальная карьера”, …), not the four large parts.
2. Send **one** of those units per day, on Telegram and in a quiet web reader.
3. It will not give you tomorrow’s chapter today.

The next book in the same spirit is Machiavelli’s *The Prince* (included from Project Gutenberg: one chapter a day for 27 days).

## Why this shape

Tarasov’s book is a sequence of parables. Reading three in a row is easy; remembering one overnight is the point. The bot treats that as a calendar, not a queue: miss a morning and you do not get two chapters the next day. Missed pages stay open in the archive; the next delivery is still just one page.

## Run it

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/). The web reader is Django 5.2, the current long-term support release.

```bash
cp .env.example .env
uv sync
uv run onechapter serve
```

Open [http://127.0.0.1:43147](http://127.0.0.1:43147).

Or with Docker:

```bash
docker compose up --build
```

### Add *Технология жизни*

The MOBI is not in this repository (it is a copyrighted book you already own). Put yours in:

- the Library page upload, or
- a Telegram chat with the bot (send the `.mobi` as a file), or
- the command line:

```bash
uv run onechapter ingest /path/to/vladimir_tarasov_tehnologiya_zhi.mobi
```

The ingest step looks up the publisher table of contents and cuts the file at those titles. Prefaces are day 1–2; *Об авторе* and the publisher catalogue are dropped. Editions differ slightly — a missing title is skipped rather than forcing a bad split. After ingest you should see about seventy short chapters, each a few hundred words.

### Telegram

You do not have to restart the app.

1. Talk to [@BotFather](https://t.me/BotFather), `/newbot`, copy the token.
2. Paste it on the Settings page and save — polling starts immediately — **or** put `TELEGRAM_BOT_TOKEN=…` in `.env` and start the server.
3. Open the bot, send `/start`, then `/begin` (or send the MOBI first).

Commands: `/today`, `/yesterday`, `/progress`, `/pause`, `/resume`, `/time 08:00`, `/tz Asia/Yerevan`, `/books`, `/read tehnologiya-zhizni`.

Default delivery is 08:00 in `Asia/Yerevan`. Change both on Settings or with `/tz` and `/time`.

## What is included

| Book | Source | Days |
|---|---|---|
| A Note on Slow Reading | original, five pages on the practice | 5 |
| The Prince | public domain, Project Gutenberg #1232 (Marriott) | 27 |
| Технология жизни | **your** MOBI / EPUB / FB2 | ~78 |

## Layout

```
src/onechapter/
  ingest/     MOBI/EPUB/FB2 → chapters (Tarasov profile + generic headings)
  bot/        Telegram commands and the once-a-minute delivery tick
  project/    Django 5.2 settings, URLs, and ASGI entry
  reader/     Django views for the local reader
  web/        templates and static files
  reading.py  calendar math (one unlock per local day)
```

Data lives in `data/` (`app.db`, uploads). It is gitignored.

## Tests

```bash
uv run pytest
```
