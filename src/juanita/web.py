# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal web frontend for juanita: paste a URL, get a Mealie recipe.

Wraps the existing CLI pipeline (fetch_video -> extract_recipe -> push_to_mealie)
behind a small FastAPI app. Extraction takes 30s+ (yt-dlp fetch + a Claude
call), so submissions run as background jobs that the page polls for status
rather than holding the HTTP connection open.

Configuration is the same as the CLI (see cli.py's module docstring), plus:
  JUANITA_WEB_TOKEN   - required; shared secret for HTTP Basic auth.
  YTDLP_COOKIES_FILE  - optional path to a cookies.txt (browser-cookie auth
                        doesn't work headless/in a container).

Run with the `juanita-web` console script, or directly via
`uvicorn juanita.web:app`.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import secrets
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

import anthropic
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from juanita.cli import Mealie, extract_recipe, fetch_video, load_config, push_to_mealie

log = logging.getLogger("juanita.web")

MAX_JOBS = 50
MAX_WORKERS = 2

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic()


@dataclasses.dataclass
class Job:
    id: str
    url: str
    status: str = "queued"  # queued | running | done | error
    recipe_name: str | None = None
    mealie_link: str | None = None
    error: str | None = None
    created_at: float = dataclasses.field(default_factory=time.time)


class _JobStore:
    """In-memory job registry, capped at `max_jobs` (oldest evicted first)."""

    def __init__(self, max_jobs: int = MAX_JOBS):
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = Lock()
        self._max = max_jobs

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._max:
                self._jobs.popitem(last=False)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        return list(reversed(list(self._jobs.values())))[:limit]


jobs = _JobStore()
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

_client: anthropic.Anthropic | None = None
_mealie: Mealie | None = None
_cookies_file: str | None = None
_init_lock = Lock()


def _ensure_initialized() -> None:
    """Load config and build the shared Anthropic/Mealie clients, once.

    Mirrors the env-var checks in cli.py's main(), so misconfiguration fails
    fast at startup instead of on the first submitted job.
    """
    global _client, _mealie, _cookies_file
    if _mealie is not None:
        return
    with _init_lock:
        if _mealie is not None:
            return
        load_config(None)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        if not os.environ.get("JUANITA_WEB_TOKEN"):
            raise RuntimeError("JUANITA_WEB_TOKEN is not set (shared secret for the web UI).")
        base, token = os.environ.get("MEALIE_URL"), os.environ.get("MEALIE_TOKEN")
        if not base or not token:
            raise RuntimeError("MEALIE_URL and MEALIE_TOKEN must be set.")
        _client = anthropic.Anthropic()
        _mealie = Mealie(base, token)
        _cookies_file = os.environ.get("YTDLP_COOKIES_FILE")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_initialized()
    yield


app = FastAPI(title="juanita", lifespan=lifespan)


def _run_job(
    job: Job, client: anthropic.Anthropic, mealie: Mealie, cookies_file: str | None,
) -> None:
    job.status = "running"
    try:
        source = fetch_video(job.url, cookies_file=cookies_file)
        recipe = extract_recipe(client, source)
        job.recipe_name = recipe.name
        slug = push_to_mealie(mealie, recipe, source)
        group = mealie.group_slug()
        job.mealie_link = (
            f"{mealie.base}/g/{group}/r/{slug}" if group else f"{mealie.base} (slug: {slug})"
        )
        job.status = "done"
    except Exception as e:  # noqa: BLE001 - surfaced to the UI, not raised
        job.error = str(e)
        job.status = "error"
        log.error("import failed for %s: %s", job.url, e)


def _check_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:  # noqa: B008
    token = os.environ.get("JUANITA_WEB_TOKEN")
    if not token or not secrets.compare_digest(credentials.password, token):
        raise HTTPException(
            status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"},
        )


class SubmitRequest(BaseModel):
    url: str


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: None = Depends(_check_auth)):
    return templates.TemplateResponse(request, "index.html", {"jobs": jobs.recent()})


@app.post("/jobs")
def submit(body: SubmitRequest, _: None = Depends(_check_auth)):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    job = Job(id=uuid.uuid4().hex[:12], url=url)
    jobs.add(job)
    executor.submit(_run_job, job, _client, _mealie, _cookies_file)
    return {"id": job.id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str, _: None = Depends(_check_auth)):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return dataclasses.asdict(job)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=8000)  # noqa: S104 - intentional container bind


if __name__ == "__main__":
    main()
