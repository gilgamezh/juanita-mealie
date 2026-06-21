# SPDX-License-Identifier: GPL-3.0-or-later
"""
Turn YouTube cooking videos (or local recipe text files) into Mealie recipes.

Pipeline per source:
  1. yt-dlp  -> title, description, thumbnail, auto-generated transcript
     (or, for a local file, just read its text)
  2. Claude  -> structured recipe JSON (name, ingredients, steps, tags)
  3. Mealie  -> create recipe, fill in details, attach the source URL + thumbnail

Configuration is read from a .env file in the current directory (see
.env.example), or from real environment variables, which take precedence:
    ANTHROPIC_API_KEY, MEALIE_URL, MEALIE_TOKEN

Usage:
    cp .env.example .env   # then fill in your keys
    youtube-to-mealie https://youtu.be/wUewR4C0I_Y https://youtu.be/rzL07v6w8AA
    # import a local recipe text file (any positional that is a file on disk):
    youtube-to-mealie grandmas-walnut-bread.txt
    # or feed a file of URLs, one per line:
    youtube-to-mealie --from-file urls.txt
    # preview the parsed recipe without touching Mealie:
    youtube-to-mealie --dry-run https://youtu.be/UlafoXGyx6g
    # -v for full tracebacks, -q to only show warnings/errors
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import stat
import sys
import time
from pathlib import Path

import anthropic
import requests
from pydantic import BaseModel, Field
from yt_dlp import YoutubeDL

MODEL = "claude-opus-4-8"

log = logging.getLogger("yt2mealie")


# ---- logging (fades style) --------------------------------------------------

FMT_SIMPLE = "*** yt2mealie ***  %(asctime)s  %(levelname)-8s %(message)s"
FMT_DETAILED = "*** yt2mealie ***  %(asctime)s  %(name)-18s %(levelname)-8s %(message)s"


def set_up_logging(verbose: bool, quiet: bool) -> None:
    """Configure the 'yt2mealie' logger, mimicking fades' format and levels."""
    log.setLevel(logging.DEBUG)
    if verbose:
        level, fmt = logging.DEBUG, FMT_DETAILED
    elif quiet:
        level, fmt = logging.WARNING, FMT_SIMPLE
    else:
        level, fmt = logging.INFO, FMT_SIMPLE
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt))
    log.addHandler(handler)


# ---- config -----------------------------------------------------------------

# Settings the tool reads, in KEY=VALUE form (also valid environment variables).
SECRET_KEYS = ("ANTHROPIC_API_KEY", "MEALIE_TOKEN")


def user_config_path() -> Path:
    """Default per-user config file, honoring XDG_CONFIG_HOME.

    e.g. ~/.config/youtube-to-mealie/config.env
    """
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / "youtube-to-mealie" / "config.env"


def load_dotenv(path: Path) -> bool:
    """Minimal .env/config loader: KEY=VALUE lines, # comments, optional quotes.

    Does not override variables already set in the environment. Returns True if
    the file existed and was read.
    """
    if not path.exists():
        return False
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
    return True


def load_config(explicit: str | None) -> None:
    """Populate os.environ from a config file (real env vars still win).

    Resolution order, first hit wins for any given key (load_dotenv uses
    setdefault, so earlier sources take precedence):
      1. --config FILE, if given (must exist).
      2. ./.env in the current directory.
      3. The per-user config (see user_config_path()).
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not load_dotenv(path):
            raise FileNotFoundError(f"config file not found: {path}")
        log.debug("loaded config from %s", path)
        return
    if load_dotenv(Path.cwd() / ".env"):
        log.debug("loaded config from ./.env")
    if load_dotenv(user_config_path()):
        log.debug("loaded config from %s", user_config_path())


def _prompt(label: str, *, secret: bool, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    ask = getpass.getpass if secret else input
    while True:
        value = ask(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if not secret:
            return ""  # allow leaving optional plain fields blank
        print("  (required — please enter a value)")


def init_config(path: Path | None) -> int:
    """Interactively create the config file, prompting for keys and secrets."""
    target = (Path(path).expanduser() if path else user_config_path())
    print(f"Creating youtube-to-mealie config at:\n  {target}\n")
    if target.exists():
        ans = input("File already exists. Overwrite? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted; existing config left untouched.")
            return 1

    print("Enter your settings (input for keys/tokens is hidden):\n")
    anthropic_key = _prompt("Anthropic API key", secret=True)
    mealie_url = _prompt("Mealie base URL (no trailing slash, blank to skip)",
                         secret=False).rstrip("/")
    mealie_token = ""
    if mealie_url:
        mealie_token = _prompt("Mealie API token", secret=True)
    cookies = _prompt("YouTube cookies-from-browser (e.g. firefox; blank for none)",
                      secret=False)

    lines = [
        "# youtube-to-mealie config. Real environment variables override these.",
        "# Regenerate with: youtube-to-mealie init",
        "",
        f"ANTHROPIC_API_KEY={anthropic_key}",
    ]
    if mealie_url:
        lines += [f"MEALIE_URL={mealie_url}", f"MEALIE_TOKEN={mealie_token}"]
    if cookies:
        lines.append(f"YTDLP_COOKIES_FROM_BROWSER={cookies}")
    lines.append("")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines))
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — it holds secrets
    print(f"\nWrote {target} (permissions 0600).")
    print("You can now run, e.g.:\n  youtube-to-mealie https://youtu.be/VIDEO_ID")
    return 0


# ---- 1. YouTube -------------------------------------------------------------

def fetch_video(url: str, *, cookies_from_browser: str | None = None,
                cookies_file: str | None = None) -> dict:
    """Return {title, description, webpage_url, thumbnail, transcript}.

    Passing browser cookies makes YouTube far less likely to 429 the caption
    download (authenticated requests are throttled much less aggressively).
    """
    opts = {
        "skip_download": True,
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitleslangs": ["en", "en-US", "en-orig"],
        "quiet": True,
        "no_warnings": True,
        # We only want metadata + captions, never the media. Without this,
        # extract_info still tries to *select* a playable format and raises
        # "Requested format is not available" for videos whose formats yt-dlp
        # can't enumerate (live/upcoming, DRM, login-walled, etc.).
        "ignore_no_formats_error": True,
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookies_file:
        opts["cookiefile"] = cookies_file
    log.debug("yt-dlp: extracting metadata for %s", url)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        # Fetch captions inside the ydl session so the request carries yt-dlp's
        # headers/cookies — a bare urllib fetch gets 429'd by YouTube fast.
        transcript = _extract_transcript(ydl, info)

    return {
        "title": info.get("title", ""),
        "description": info.get("description", "") or "",
        "source_url": info.get("webpage_url", url),
        "thumbnail": info.get("thumbnail"),
        "body": transcript,
    }


# ---- 1b. Local text files ---------------------------------------------------

def load_text_file(path: str) -> dict:
    """Read a local recipe text file into the same record shape as fetch_video.

    The whole file is the body; the first non-blank line seeds the title (Claude
    still produces the final recipe name). There's no source URL or thumbnail.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        raise RuntimeError(f"file is empty: {path}")
    title = next((ln.strip() for ln in text.splitlines() if ln.strip()), p.stem)
    return {
        "title": title,
        "description": "",
        "source_url": None,
        "thumbnail": None,
        "body": text,
    }


def _extract_transcript(ydl: YoutubeDL, info: dict) -> str:
    """Pull the plain-text transcript from yt-dlp's caption tracks."""
    tracks = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for lang in ("en", "en-US", "en-orig"):
        for source in (tracks, auto):
            if lang in source:
                fmt = next(
                    (f for f in source[lang] if f.get("ext") == "json3"),
                    source[lang][0],
                )
                return _download_caption(ydl, fmt["url"], fmt.get("ext"))
    log.warning("no English captions found; proceeding without a transcript")
    return ""


def _download_caption(ydl: YoutubeDL, url: str, ext: str | None, attempts: int = 4) -> str:
    """GET a caption track through yt-dlp's HTTP client, retrying on 429."""
    delay = 3
    for attempt in range(1, attempts + 1):
        try:
            raw = ydl.urlopen(url).read().decode("utf-8", "replace")
            break
        except Exception as e:  # noqa: BLE001 - retry only on rate-limit
            if "429" not in str(e):
                raise
            if attempt < attempts:
                log.warning("caption fetch rate-limited (429); retry %d/%d in %ds",
                            attempt, attempts - 1, delay)
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(
                "YouTube rate-limited the caption download (HTTP 429) after "
                f"{attempts} attempts. This is usually transient — retry in a few "
                "minutes, or pass browser cookies to authenticate the request: "
                "--cookies-from-browser firefox|chrome|... (or --cookies cookies.txt)."
            ) from e
    if ext == "json3":
        data = json.loads(raw)
        parts = []
        for event in data.get("events", []):
            for seg in event.get("segs", []) or []:
                parts.append(seg.get("utf8", ""))
        return "".join(parts).strip()
    # vtt/srt fallback: drop cue headers and timestamps
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or "-->" in line or line.isdigit() or line == "WEBVTT":
            continue
        lines.append(line)
    return "\n".join(lines)


# ---- 2. Claude --------------------------------------------------------------

class Recipe(BaseModel):
    name: str = Field(description="Short recipe title, e.g. 'Creamy Sesame Ginger Dressing'")
    description: str = Field(description="One or two appetizing sentences. No URLs.")
    recipe_yield: str = Field(description="Yield/servings, e.g. '4 servings' or '1 jar'")
    ingredients: list[str] = Field(
        description="Each ingredient as one line, with quantity if stated")
    instructions: list[str] = Field(description="Ordered steps, one per item")
    tags: list[str] = Field(description="3-6 lowercase kebab-case tags")


def extract_recipe(client: anthropic.Anthropic, source: dict) -> Recipe:
    body = source["body"] or "(no source text available)"
    description = source.get("description") or ""
    prompt = (
        "You are extracting a clean, cookable recipe from the source material below "
        "(a cooking-video transcript or someone's written recipe notes).\n"
        "Use the SOURCE as the primary content; the title and description add context.\n"
        "Infer reasonable quantities only when the source clearly implies them; otherwise "
        "describe the ingredient without a fabricated amount. Do not invent steps.\n"
        "Write the recipe in the same language as the source.\n\n"
        f"TITLE: {source['title']}\n\n"
        f"DESCRIPTION:\n{description[:4000]}\n\n"
        f"SOURCE:\n{body[:120000]}"
    )
    log.info("calling Claude (%s, %d source chars)...", MODEL, len(body))
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
        output_format=Recipe,
    )
    log.debug(
        "claude: stop_reason=%s, tokens in=%s out=%s",
        resp.stop_reason, resp.usage.input_tokens, resp.usage.output_tokens,
    )
    if resp.parsed_output is None:
        raise RuntimeError(
            f"Claude did not return a parseable recipe (stop_reason={resp.stop_reason})")
    return resp.parsed_output


# ---- 3. Mealie --------------------------------------------------------------

def _check(r: requests.Response) -> requests.Response:
    """raise_for_status, but surface Mealie's JSON error body in the message."""
    if not r.ok:
        try:
            detail = json.dumps(r.json(), indent=2, ensure_ascii=False)
        except ValueError:
            detail = r.text[:1000]
        raise RuntimeError(f"{r.request.method} {r.url} -> {r.status_code}\n{detail}")
    return r


class Mealie:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        self._tag_cache: dict[str, dict] = {}

    def create(self, name: str) -> str:
        r = self.s.post(f"{self.base}/api/recipes", json={"name": name}, timeout=30)
        return _check(r).json()  # endpoint returns the new slug as a JSON string

    def get(self, slug: str) -> dict:
        return _check(self.s.get(f"{self.base}/api/recipes/{slug}", timeout=30)).json()

    def update(self, slug: str, recipe: dict) -> dict:
        return _check(self.s.put(f"{self.base}/api/recipes/{slug}", json=recipe, timeout=30)).json()

    def resolve_tag(self, name: str) -> dict:
        """Return a full Mealie tag object, creating it if it doesn't exist.

        Recipes can only reference existing organizer tags; passing a bare
        {name, slug} in the recipe PUT makes Mealie fail with the misleading
        "Recipe already exists". So create/look up the tag here and attach the
        full object (with id and groupId) instead.
        """
        key = name.lower()
        if key in self._tag_cache:
            return self._tag_cache[key]
        created = self.s.post(f"{self.base}/api/organizers/tags", json={"name": name}, timeout=30)
        if created.status_code == 201:
            tag = created.json()
        else:
            # Most likely it already exists -> look it up by name.
            found = _check(
                self.s.get(
                    f"{self.base}/api/organizers/tags",
                    params={"search": name, "perPage": 100},
                    timeout=30,
                )
            ).json()
            tag = next((t for t in found.get("items", []) if t["name"].lower() == key), None)
            if tag is None:
                _check(created)  # not a conflict either -> raise the real error
                raise RuntimeError(f"could not resolve tag {name!r}")
        self._tag_cache[key] = tag
        return tag

    def set_image_from_url(self, slug: str, image_url: str) -> None:
        r = self.s.post(
            f"{self.base}/api/recipes/{slug}/image",
            json={"url": image_url, "includeTags": False},
            timeout=60,
        )
        _check(r)


def push_to_mealie(mealie: Mealie, recipe: Recipe, source: dict, *,
                   include_tags: bool = True) -> str:
    slug = mealie.create(recipe.name)
    log.debug("mealie: created recipe slug %s", slug)
    doc = mealie.get(slug)

    source_url = source.get("source_url")
    description = recipe.description.strip()
    if source_url and source_url not in description:
        description = f"{description}\n\nSource: {source_url}".strip()

    # NB: don't overwrite doc["name"] — Mealie already set it from create() and
    # may have de-duplicated it (e.g. "Foo (1)"). Re-setting the base name forces
    # a re-slug to an already-taken slug -> 400 "Recipe already exists".
    doc["description"] = description
    doc["recipeYield"] = recipe.recipe_yield
    if source_url:
        doc["orgURL"] = source_url
    doc["recipeIngredient"] = [{"note": i} for i in recipe.ingredients]
    doc["recipeInstructions"] = [{"text": s} for s in recipe.instructions]
    if include_tags and recipe.tags:
        doc["tags"] = [mealie.resolve_tag(t) for t in recipe.tags]
        log.debug("mealie: attached tags %s", [t["name"] for t in doc["tags"]])

    mealie.update(slug, doc)

    if source.get("thumbnail"):
        try:
            mealie.set_image_from_url(slug, source["thumbnail"])
            log.debug("mealie: set image from %s", source["thumbnail"])
        except Exception as e:  # noqa: BLE001 - image is best-effort
            log.warning("could not set image for %s: %s", slug, e)

    return slug


# ---- CLI --------------------------------------------------------------------

def _init_command(argv: list[str]) -> int:
    ip = argparse.ArgumentParser(
        prog="youtube-to-mealie init",
        description="Interactively create the config file (stored in your home by default).",
    )
    ip.add_argument("-c", "--config", metavar="FILE",
                    help="Write the config to FILE instead of the default user path")
    args = ip.parse_args(argv)
    set_up_logging(False, False)
    return init_config(Path(args.config) if args.config else None)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # `youtube-to-mealie init [...]` creates the config file and exits.
    if argv and argv[0] == "init":
        return _init_command(argv[1:])

    ap = argparse.ArgumentParser(
        description="Import recipes into Mealie from YouTube videos or local text files.")
    ap.add_argument("urls", nargs="*", metavar="SOURCE",
                    help="YouTube URLs and/or paths to local recipe text files")
    ap.add_argument("-c", "--config", metavar="FILE",
                    help="Path to a config file (KEY=VALUE). Run `youtube-to-mealie init` "
                         "to create one. Defaults to ./.env then the per-user config.")
    ap.add_argument("--from-file", help="Read URLs from a file, one per line")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the parsed recipe, don't push to Mealie")
    ap.add_argument("--no-tags", action="store_true", help="Don't attach tags to recipes")
    ap.add_argument("--cookies-from-browser", metavar="BROWSER",
                    help="Load YouTube cookies from a browser (firefox, chrome, ...) "
                         "to avoid caption-download rate limits (HTTP 429)")
    ap.add_argument("--cookies", metavar="FILE",
                    help="Path to a cookies.txt file (alternative to --cookies-from-browser)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Verbose logging (full tracebacks)")
    ap.add_argument("-q", "--quiet", action="store_true", help="Only log warnings and errors")
    args = ap.parse_args(argv)

    set_up_logging(args.verbose, args.quiet)

    # Load config: --config FILE, else ./.env, else the per-user config.
    # Real environment variables always take precedence.
    try:
        load_config(args.config)
    except FileNotFoundError as e:
        ap.error(str(e))

    # Cookies: CLI flag wins, else YTDLP_COOKIES_FROM_BROWSER from env/.env.
    cookies_from_browser = args.cookies_from_browser or os.environ.get("YTDLP_COOKIES_FROM_BROWSER")

    sources = list(args.urls)
    if args.from_file:
        with open(args.from_file) as f:
            sources += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not sources:
        ap.error("no inputs given (pass YouTube URLs and/or local recipe text files)")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        ap.error("ANTHROPIC_API_KEY is not set. Run `youtube-to-mealie init` to create a "
                 "config, or set it in the environment / a --config file.")
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    mealie = None
    if not args.dry_run:
        base, token = os.environ.get("MEALIE_URL"), os.environ.get("MEALIE_TOKEN")
        if not base or not token:
            ap.error("set MEALIE_URL and MEALIE_TOKEN (run `youtube-to-mealie init`, "
                     "use a --config file, or pass --dry-run)")
        mealie = Mealie(base, token)

    failures = 0
    for item in sources:
        try:
            log.info("processing %s", item)
            # A local recipe text file vs. a URL to fetch with yt-dlp.
            if os.path.isfile(item):
                source = load_text_file(item)
            else:
                source = fetch_video(item, cookies_from_browser=cookies_from_browser,
                                     cookies_file=args.cookies)
            log.info("source: %r (%d chars)", source["title"], len(source["body"]))
            recipe = extract_recipe(client, source)
            log.info(
                "recipe: %r (%d ingredients, %d steps)",
                recipe.name, len(recipe.ingredients), len(recipe.instructions),
            )
            if args.dry_run:
                print(json.dumps(recipe.model_dump(), indent=2, ensure_ascii=False))
                continue
            slug = push_to_mealie(mealie, recipe, source, include_tags=not args.no_tags)
            log.info("imported -> %s/g/home/r/%s", mealie.base, slug)
        except Exception as e:  # noqa: BLE001 - keep going through the batch
            failures += 1
            log.error("failed on %s: %s", item, e)
            log.debug("traceback", exc_info=True)

    if failures:
        log.warning("%d of %d failed", failures, len(sources))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
