# youtube-to-mealie

Turn YouTube cooking videos into [Mealie](https://mealie.io) recipes. It pulls
the auto-generated transcript with [`yt-dlp`](https://github.com/yt-dlp/yt-dlp),
has [Claude](https://www.anthropic.com/claude) extract a structured recipe, and
creates it in Mealie via the API — including the source video URL and thumbnail.

## Install

Requires Python 3.11+.

With [`uv`](https://docs.astral.sh/uv/) (recommended):

```bash
uv tool install youtube-to-mealie
# or run without installing:
uvx youtube-to-mealie --help
```

With `pip`:

```bash
pip install youtube-to-mealie
```

> Not yet on PyPI? Install straight from source:
> `pip install git+https://github.com/gilgamezh/youtube-to-mealie`

## Configure

Copy the example env file and fill in your keys:

```bash
cp .env.example .env   # ANTHROPIC_API_KEY, MEALIE_URL, MEALIE_TOKEN
```

`.env` is read from the current working directory; real environment variables
take precedence. Create the Mealie token in the UI: **Profile → API Tokens →
create**. The Anthropic key comes from <https://console.anthropic.com/settings/keys>.

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Used for recipe extraction. |
| `MEALIE_URL` | unless `--dry-run` | Base URL of your Mealie instance, no trailing slash. |
| `MEALIE_TOKEN` | unless `--dry-run` | Long-lived Mealie API token. |
| `YTDLP_COOKIES_FROM_BROWSER` | no | Browser name for cookie loading (see below). |

## Usage

```bash
# One or more videos
youtube-to-mealie https://youtu.be/wUewR4C0I_Y https://youtu.be/rzL07v6w8AA

# Preview the parsed recipe without writing to Mealie (no token needed)
youtube-to-mealie --dry-run https://youtu.be/UlafoXGyx6g

# Batch from a file (one URL per line, # comments allowed)
youtube-to-mealie --from-file urls.txt

# Skip tag creation/attachment
youtube-to-mealie --no-tags https://youtu.be/VIDEO_ID
```

Each video is handled independently — one failure doesn't stop the batch, and
the exit code is non-zero if any video failed. Use `-v` for full tracebacks,
`-q` to only show warnings and errors.

### Caption rate limits (HTTP 429)

YouTube sometimes rate-limits transcript (caption) downloads with `HTTP 429`.
The tool retries with backoff, but a sustained 429 is best solved by passing
browser cookies so the request is authenticated:

```bash
youtube-to-mealie --cookies-from-browser firefox https://youtu.be/VIDEO_ID
# or a cookies.txt export:
youtube-to-mealie --cookies cookies.txt https://youtu.be/VIDEO_ID
# or set YTDLP_COOKIES_FROM_BROWSER in .env
```

If you don't have cookies handy, waiting a few minutes usually clears it.

## How it works

1. **yt-dlp** extracts the title, description, thumbnail, and auto-generated
   transcript (no video download).
2. **Claude** turns the transcript into a structured recipe (name, description,
   yield, ingredients, instructions, tags) via structured outputs.
3. **Mealie** gets a new recipe created and filled in via its REST API, with the
   source URL set as `orgURL` and the YouTube thumbnail as the image.

See [CLAUDE.md](./CLAUDE.md) for the detailed pipeline and design notes.

## Notes

- Recipes are created **directly via the live Mealie API**. Re-running the same
  video creates a duplicate (Mealie appends `-1`, `-2`, … to the slug).
- The YouTube thumbnail is used as the recipe image. Mealie can't scrape recipes
  straight from YouTube URLs, which is why this transcript→LLM route exists.

## Development

```bash
git clone https://github.com/gilgamezh/youtube-to-mealie
cd youtube-to-mealie
uv pip install -e ".[dev]"   # or: pip install -e ".[dev]"
ruff check src
```

## License

[GPL-3.0-or-later](./LICENSE).
