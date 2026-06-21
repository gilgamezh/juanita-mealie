# SPDX-License-Identifier: GPL-3.0-or-later
"""
Turn YouTube cooking videos into Mealie recipes.

Pipeline per video:
  1. yt-dlp  -> title, description, thumbnail, auto-generated transcript
  2. Claude  -> structured recipe JSON (name, ingredients, steps, tags)
  3. Mealie  -> create recipe, fill in details, attach the video URL + thumbnail

Configuration is read from a .env file in the current directory (see
.env.example), or from real environment variables, which take precedence:
    ANTHROPIC_API_KEY, MEALIE_URL, MEALIE_TOKEN

Usage:
    cp .env.example .env   # then fill in your keys
    youtube-to-mealie https://youtu.be/wUewR4C0I_Y https://youtu.be/rzL07v6w8AA
    # or feed a file of URLs, one per line:
    youtube-to-mealie --from-file urls.txt
    # preview the parsed recipe without touching Mealie:
    youtube-to-mealie --dry-run https://youtu.be/UlafoXGyx6g
    # -v for full tracebacks, -q to only show warnings/errors
"""
from __future__ import annotations

import argparse
import json
import logging
import os
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


# ---- .env -------------------------------------------------------------------

def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, # comments, optional quotes.
    Does not override variables already set in the environment."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


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
        "webpage_url": info.get("webpage_url", url),
        "thumbnail": info.get("thumbnail"),
        "transcript": transcript,
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
    ingredients: list[str] = Field(description="Each ingredient as one line, with quantity if stated")
    instructions: list[str] = Field(description="Ordered steps, one per item")
    tags: list[str] = Field(description="3-6 lowercase kebab-case tags")


def extract_recipe(client: anthropic.Anthropic, video: dict) -> Recipe:
    transcript = video["transcript"] or "(no transcript available)"
    prompt = (
        "You are extracting a clean, cookable recipe from a YouTube cooking video.\n"
        "Use the transcript as the primary source; the title and description add context.\n"
        "Infer reasonable quantities only when the video clearly implies them; otherwise "
        "describe the ingredient without a fabricated amount. Do not invent steps.\n\n"
        f"TITLE: {video['title']}\n\n"
        f"DESCRIPTION:\n{video['description'][:4000]}\n\n"
        f"TRANSCRIPT:\n{transcript[:120000]}"
    )
    log.info("calling Claude (%s, %d transcript chars)...", MODEL, len(transcript))
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
        raise RuntimeError(f"Claude did not return a parseable recipe (stop_reason={resp.stop_reason})")
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


def push_to_mealie(mealie: Mealie, recipe: Recipe, video: dict, *, include_tags: bool = True) -> str:
    slug = mealie.create(recipe.name)
    log.debug("mealie: created recipe slug %s", slug)
    doc = mealie.get(slug)

    source = video["webpage_url"]
    description = recipe.description.strip()
    if source not in description:
        description = f"{description}\n\nSource: {source}".strip()

    # NB: don't overwrite doc["name"] — Mealie already set it from create() and
    # may have de-duplicated it (e.g. "Foo (1)"). Re-setting the base name forces
    # a re-slug to an already-taken slug -> 400 "Recipe already exists".
    doc["description"] = description
    doc["recipeYield"] = recipe.recipe_yield
    doc["orgURL"] = source
    doc["recipeIngredient"] = [{"note": i} for i in recipe.ingredients]
    doc["recipeInstructions"] = [{"text": s} for s in recipe.instructions]
    if include_tags and recipe.tags:
        doc["tags"] = [mealie.resolve_tag(t) for t in recipe.tags]
        log.debug("mealie: attached tags %s", [t["name"] for t in doc["tags"]])

    mealie.update(slug, doc)

    if video.get("thumbnail"):
        try:
            mealie.set_image_from_url(slug, video["thumbnail"])
            log.debug("mealie: set image from %s", video["thumbnail"])
        except Exception as e:  # noqa: BLE001 - image is best-effort
            log.warning("could not set image for %s: %s", slug, e)

    return slug


# ---- CLI --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Import YouTube cooking videos into Mealie.")
    ap.add_argument("urls", nargs="*", help="YouTube URLs")
    ap.add_argument("--from-file", help="Read URLs from a file, one per line")
    ap.add_argument("--dry-run", action="store_true", help="Print the parsed recipe, don't push to Mealie")
    ap.add_argument("--no-tags", action="store_true", help="Don't attach tags to recipes")
    ap.add_argument("--cookies-from-browser", metavar="BROWSER",
                    help="Load YouTube cookies from a browser (firefox, chrome, ...) "
                         "to avoid caption-download rate limits (HTTP 429)")
    ap.add_argument("--cookies", metavar="FILE", help="Path to a cookies.txt file (alternative to --cookies-from-browser)")
    ap.add_argument("-v", "--verbose", action="store_true", help="Verbose logging (full tracebacks)")
    ap.add_argument("-q", "--quiet", action="store_true", help="Only log warnings and errors")
    args = ap.parse_args()

    set_up_logging(args.verbose, args.quiet)

    # Load .env from the current working directory (real env vars still win).
    load_dotenv(Path.cwd() / ".env")

    # Cookies: CLI flag wins, else YTDLP_COOKIES_FROM_BROWSER from env/.env.
    cookies_from_browser = args.cookies_from_browser or os.environ.get("YTDLP_COOKIES_FROM_BROWSER")

    urls = list(args.urls)
    if args.from_file:
        with open(args.from_file) as f:
            urls += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not urls:
        ap.error("no URLs given")

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    mealie = None
    if not args.dry_run:
        base, token = os.environ.get("MEALIE_URL"), os.environ.get("MEALIE_TOKEN")
        if not base or not token:
            ap.error("set MEALIE_URL and MEALIE_TOKEN (or use --dry-run)")
        mealie = Mealie(base, token)

    failures = 0
    for url in urls:
        try:
            log.info("processing %s", url)
            video = fetch_video(url, cookies_from_browser=cookies_from_browser,
                                cookies_file=args.cookies)
            log.info("video: %r (transcript: %d chars)", video["title"], len(video["transcript"]))
            recipe = extract_recipe(client, video)
            log.info(
                "recipe: %r (%d ingredients, %d steps)",
                recipe.name, len(recipe.ingredients), len(recipe.instructions),
            )
            if args.dry_run:
                print(json.dumps(recipe.model_dump(), indent=2, ensure_ascii=False))
                continue
            slug = push_to_mealie(mealie, recipe, video, include_tags=not args.no_tags)
            log.info("imported -> %s/g/home/r/%s", mealie.base, slug)
        except Exception as e:  # noqa: BLE001 - keep going through the batch
            failures += 1
            log.error("failed on %s: %s", url, e)
            log.debug("traceback", exc_info=True)

    if failures:
        log.warning("%d of %d failed", failures, len(urls))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
