# youtube-to-mealie

Turn YouTube cooking videos into [Mealie](https://mealie.io) recipes. It pulls
the auto-generated transcript with [`yt-dlp`](https://github.com/yt-dlp/yt-dlp),
has [Claude](https://www.anthropic.com/claude) extract a structured recipe, and
creates it in Mealie via the API — including the source video URL and thumbnail.

## Install & run

Requires Python 3.11+.

### With fades (recommended)

[**fades**](https://github.com/PyAr/fades) creates and manages the virtual
environment for you, installing the dependency on first run — no global install,
no manual venv:

```bash
fades -d youtube-to-mealie -x youtube-to-mealie -- https://youtu.be/VIDEO_ID
```

`-d` declares the dependency and `-x` runs the installed `youtube-to-mealie`
console script inside the managed venv. A handy alias keeps invocations short:

```bash
alias yt2mealie='fades -d youtube-to-mealie -x youtube-to-mealie --'
yt2mealie --dry-run https://youtu.be/VIDEO_ID
```

### With pip / uv

```bash
pip install youtube-to-mealie        # or: uv tool install youtube-to-mealie
youtube-to-mealie https://youtu.be/VIDEO_ID
```

> Not yet on PyPI? Install straight from source by replacing the dependency name
> with `git+https://github.com/gilgamezh/youtube-to-mealie` in any command above.

## Configure

The quickest way is the interactive setup, which writes a config file to your
home directory (`~/.config/youtube-to-mealie/config.env`, mode `0600`):

```bash
youtube-to-mealie init
# with fades: fades -d youtube-to-mealie -x youtube-to-mealie -- init
```

It prompts for your Anthropic API key, Mealie URL + token (optional — skip for
`--dry-run` only), and optional YouTube cookies. After that, just run the tool
and it picks up the config automatically.

Prefer to manage it yourself? Settings are read, in order (first hit wins; real
environment variables always override):

1. `--config FILE` / `-c FILE` — an explicit config file.
2. `./.env` in the current directory (see [`.env.example`](./.env.example)).
3. `~/.config/youtube-to-mealie/config.env` — the per-user config.

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Used for recipe extraction. <https://console.anthropic.com/settings/keys> |
| `MEALIE_URL` | unless `--dry-run` | Base URL of your Mealie instance, no trailing slash. |
| `MEALIE_TOKEN` | unless `--dry-run` | Long-lived token: Mealie UI → Profile → API Tokens → create. |
| `YTDLP_COOKIES_FROM_BROWSER` | no | Browser name for cookie loading (see below). |

You can keep several profiles and pick one per run:

```bash
youtube-to-mealie -c ~/configs/home-mealie.env https://youtu.be/VIDEO_ID
```

## Usage

Examples use the bare `youtube-to-mealie` command (prefix with `fades -d
youtube-to-mealie -x youtube-to-mealie --` or use the alias above if you didn't
install it globally):

```bash
# One or more videos
youtube-to-mealie https://youtu.be/wUewR4C0I_Y https://youtu.be/rzL07v6w8AA

# Preview the parsed recipe without writing to Mealie (no Mealie token needed)
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
# or set YTDLP_COOKIES_FROM_BROWSER in your config
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
