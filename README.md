# Juanita

**Juanita** imports recipes into [Mealie](https://mealie.io) from wherever they
live. Point her at a YouTube cooking video or a local text file of notes and she
does the prep: pulls the content (transcript via
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp), or just reads the file), has
[Claude](https://www.anthropic.com/claude) extract a structured recipe in the
source's own language, and creates it in Mealie via the API — ingredients linked
to your foods database, with the source URL and thumbnail attached when available.

> Named after the unseen TV-cooking helper who quietly did all the prep and
> dirty work while the star took the spotlight. That's the job.

The PyPI distribution is `juanita-mealie`; the command you run is `juanita`.

## Install & run

Requires Python 3.11+.

### With fades (recommended)

[**fades**](https://github.com/PyAr/fades) creates and manages the virtual
environment for you, installing the dependency on first run — no global install,
no manual venv:

```bash
fades -d juanita-mealie -x juanita -- https://youtu.be/VIDEO_ID
```

`-d` declares the dependency and `-x` runs the installed `juanita` console
script inside the managed venv. A handy alias keeps invocations short:

```bash
alias juanita='fades -d juanita-mealie -x juanita --'
juanita --dry-run https://youtu.be/VIDEO_ID
```

### With pip

```bash
pip install juanita-mealie
juanita https://youtu.be/VIDEO_ID
```

> Not yet on PyPI? Install straight from source by replacing the dependency name
> with `git+https://github.com/gilgamezh/juanita-mealie` in any command above.

## Configure

The quickest way is the interactive setup, which writes a config file to your
home directory (`~/.config/juanita/config.env`, mode `0600`):

```bash
juanita init
# with fades: fades -d juanita-mealie -x juanita -- init
```

It prompts for your Anthropic API key, Mealie URL + token (optional — skip for
`--dry-run` only), and optional YouTube cookies. After that, just run the tool
and it picks up the config automatically.

Prefer to manage it yourself? Settings are read, in order (first hit wins; real
environment variables always override):

1. `--config FILE` / `-c FILE` — an explicit config file.
2. `./.env` in the current directory (see [`.env.example`](./.env.example)).
3. `~/.config/juanita/config.env` — the per-user config.

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Used for recipe extraction. <https://console.anthropic.com/settings/keys> |
| `MEALIE_URL` | unless `--dry-run` | Base URL of your Mealie instance, no trailing slash. |
| `MEALIE_TOKEN` | unless `--dry-run` | Long-lived token: Mealie UI → Profile → API Tokens → create. |
| `YTDLP_COOKIES_FROM_BROWSER` | no | Browser name for cookie loading (see below). |

You can keep several profiles and pick one per run:

```bash
juanita -c ~/configs/home-mealie.env https://youtu.be/VIDEO_ID
```

## Usage

Examples use the bare `juanita` command (prefix with `fades -d juanita-mealie -x
juanita --` or use the alias above if you didn't install it globally):

```bash
# One or more videos
juanita https://youtu.be/wUewR4C0I_Y https://youtu.be/rzL07v6w8AA

# A local recipe text file — any positional that's a file on disk is read as
# recipe notes instead of fetched as a URL. Videos and files can be mixed.
juanita grandmas-walnut-bread.txt https://youtu.be/VIDEO_ID

# Preview the parsed recipe without writing to Mealie (no Mealie token needed)
juanita --dry-run https://youtu.be/UlafoXGyx6g

# Batch from a file (one URL per line, # comments allowed)
juanita --from-file urls.txt

# Skip tag creation/attachment
juanita --no-tags https://youtu.be/VIDEO_ID

# Store ingredients as plain text instead of linking them to your foods DB
juanita --not-linked-ingredients https://youtu.be/VIDEO_ID
```

Each source is handled independently — one failure doesn't stop the batch, and
the exit code is non-zero if any source failed. Use `-v` for full tracebacks,
`-q` to only show warnings and errors.

### Local text files

Pass a path to any text file containing a recipe (ingredients, steps — however
roughly noted) and it's sent straight to Claude; no URL, transcript, or cookies
involved. The recipe is written in the source's own language, and since there's
no source URL or thumbnail, those Mealie fields are simply left unset.

### Caption rate limits (HTTP 429)

YouTube sometimes rate-limits transcript (caption) downloads with `HTTP 429`.
Juanita retries with backoff, but a sustained 429 is best solved by passing
browser cookies so the request is authenticated:

```bash
juanita --cookies-from-browser firefox https://youtu.be/VIDEO_ID
# or a cookies.txt export:
juanita --cookies cookies.txt https://youtu.be/VIDEO_ID
# or set YTDLP_COOKIES_FROM_BROWSER in your config
```

If you don't have cookies handy, waiting a few minutes usually clears it.

## How it works

1. Each input becomes a common source record. For a **URL**, `yt-dlp` extracts
   the title, description, thumbnail, and auto-generated transcript (no video
   download); for a **local file**, the text is read as-is.
2. **Claude** turns the source into a structured recipe (name, description,
   yield, instructions, tags, and ingredients split into quantity/unit/food/note)
   via structured outputs, in the source's own language.
3. **Mealie** gets a new recipe created and filled in via its REST API. Each
   ingredient's **food and unit are linked to your Mealie database** (looked up,
   or created if new), so amounts parse and ingredients aggregate in shopping
   lists instead of being plain text — pass `--not-linked-ingredients` to keep
   them as plain text and leave your database untouched. The source URL is set as
   `orgURL` and the thumbnail as the image, when available.

See [CLAUDE.md](./CLAUDE.md) for the detailed pipeline and design notes.

## Notes

- Recipes are created **directly via the live Mealie API**. Re-running the same
  source creates a duplicate (Mealie appends `-1`, `-2`, … to the slug).
- For videos, the YouTube thumbnail is used as the recipe image. Mealie can't
  scrape recipes straight from YouTube URLs, which is why this transcript→LLM
  route exists.

## Development

Run Juanita straight from your checkout with [fades](https://github.com/PyAr/fades)
— `bin/run_dev` installs this project editable in a managed venv (your changes
under `src/` are picked up on the next run) and forwards its arguments:

```bash
git clone https://github.com/gilgamezh/juanita-mealie
cd juanita-mealie
bin/run_dev --dry-run https://youtu.be/VIDEO_ID
bin/run_dev init
```

Added a new dependency to `pyproject.toml`? Rebuild the venv with
`FADES_REBUILD=1 bin/run_dev ...`.

Lint with ruff:

```bash
fades -d ruff -x ruff -- check src
```

## License

[GPL-3.0-or-later](./LICENSE).
