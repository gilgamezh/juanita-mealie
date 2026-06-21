# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## What this is

`youtube-to-mealie` is a CLI that turns a YouTube cooking video into a recipe in
[Mealie](https://mealie.io): it pulls the auto-generated transcript with
`yt-dlp`, has Claude extract a structured recipe, and creates it via the Mealie
REST API (with the source URL and thumbnail).

## Layout

```
src/youtube_to_mealie/
  __init__.py   # package version + public re-exports
  __main__.py   # `python -m youtube_to_mealie`
  cli.py        # everything: fetch -> extract -> push, plus argparse main()
pyproject.toml  # packaging (hatchling, src layout), console script, ruff config
```

The console script `youtube-to-mealie` maps to `youtube_to_mealie.cli:main`.

## Pipeline (all in `cli.py`)

1. **Fetch** (`fetch_video`) — `yt-dlp` extracts metadata without downloading the
   video: `title`, `description`, `webpage_url`, `thumbnail`, and the
   auto-generated captions. The transcript is parsed from the `json3` caption
   track (`_extract_transcript` / `_download_caption`), preferring `en`, then
   `en-US`, then `en-orig`, with a VTT/SRT fallback. Captions are fetched through
   `ydl.urlopen` (yt-dlp's HTTP client, with its headers/cookies) rather than a
   bare `urllib` request, and `_download_caption` retries on `HTTP 429` with
   exponential backoff. Optional browser cookies (`cookiesfrombrowser` /
   `cookiefile`) authenticate the request to dodge sustained 429s; on a final
   429 it raises a friendly error pointing at the cookie flags.

2. **Extract** (`extract_recipe`) — Claude (`claude-opus-4-8`, adaptive thinking,
   `effort: high`) turns the transcript + title + description into a validated
   `Recipe` via `messages.parse()` with a Pydantic schema (structured outputs):
   `name`, `description`, `recipe_yield`, `ingredients[]`, `instructions[]`,
   `tags[]`. The prompt forbids inventing steps or fabricating quantities.

3. **Push** (`push_to_mealie`) — against the Mealie REST API:
   - `POST /api/recipes` `{name}` → returns the new slug.
   - `GET /api/recipes/{slug}` → full recipe document.
   - Merge: set description/yield, `orgURL` = video URL, append `Source: <url>`
     to the description, map ingredients to `[{note}]` and instructions to
     `[{text}]`, resolve tags to full organizer-tag objects.
   - `PUT /api/recipes/{slug}` with the merged document.
   - `POST /api/recipes/{slug}/image` `{url: thumbnail}` to set the image
     (failure here is non-fatal — logged and skipped).

## Configuration

`load_config()` populates `os.environ` from the first config source that exists,
using `setdefault` so real environment variables always win:

1. `--config FILE` / `-c FILE` (must exist, else `ap.error`).
2. `./.env` in the current directory.
3. `user_config_path()` — `$XDG_CONFIG_HOME/youtube-to-mealie/config.env`,
   defaulting to `~/.config/youtube-to-mealie/config.env`.

All files share the same `KEY=VALUE` format (`load_dotenv`). The `init`
subcommand (`youtube-to-mealie init`, handled in `main()` before argparse via
`_init_command` → `init_config`) interactively prompts and writes the per-user
config with mode `0600` (it holds secrets — `SECRET_KEYS` are read via
`getpass`). `init` accepts `-c FILE` to write elsewhere.

Settings:

- `ANTHROPIC_API_KEY` — required.
- `MEALIE_URL` — required unless `--dry-run`.
- `MEALIE_TOKEN` — required unless `--dry-run`; a long-lived Mealie API token.
- `YTDLP_COOKIES_FROM_BROWSER` — optional; browser name for cookie loading.

## Conventions & decisions

- Secrets live only in `.env`, which is gitignored. Never commit real keys; keep
  `.env.example` placeholder-only.
- `src/` layout, packaged with hatchling. Keep the public API re-exported from
  `__init__.py` in sync when adding top-level functions.
- `Recipe`'s Pydantic schema must use only structured-output-safe constructs
  (plain strings and string arrays — no min/max length constraints).
- Idempotency is **not** handled — re-importing a video creates a duplicate
  (Mealie appends `-1`, `-2`, … to the slug).
- When updating a recipe, don't overwrite `doc["name"]`: Mealie set it on
  create and may have de-duplicated it; re-setting forces a re-slug to a taken
  slug → 400 "Recipe already exists".
- Direct API writes, not the Mealie zip/migration importer.

## Dev workflow

```bash
uv pip install -e ".[dev]"   # or: pip install -e ".[dev]"
ruff check src               # lint
youtube-to-mealie --dry-run https://youtu.be/VIDEO_ID   # no Mealie creds needed
```

There is no test suite yet; `--dry-run` is the quickest end-to-end smoke check
(it exercises fetch + extract without writing to Mealie).
