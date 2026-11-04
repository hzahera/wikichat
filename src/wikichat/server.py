"""FastAPI app: chat API (server-sent events) plus the web UI."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings, get_settings
from .index import SearchIndex
from .llm import LLM
from .security import SecurityMiddleware, install_secret_filter

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
LOGO_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
              ".jpeg": "image/jpeg", ".webp": "image/webp"}


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list, max_length=20)


def retrieval_query(message: str, history: list) -> str:
    """Short follow-ups ("and his email?") borrow words from the previous question."""
    if len(message.split()) < 6:
        turns = [t if isinstance(t, dict) else t.model_dump() for t in history]
        prev = [t["content"] for t in turns if t.get("role") == "user" and t.get("content")]
        if prev:
            return f"{prev[-1]} {message}"
    return message


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def org_logo_path(settings: Settings) -> Path | None:
    if not settings.org_logo_file:
        return None
    p = Path(settings.org_logo_file).expanduser()
    if p.suffix.lower() in LOGO_TYPES and p.is_file():
        return p
    log.warning("WIKICHAT_ORG_LOGO_FILE is not a .svg/.png/.jpg/.webp file: %s", p)
    return None


def create_app(settings: Settings | None = None, index: SearchIndex | None = None) -> FastAPI:
    settings = settings or get_settings()
    install_secret_filter(settings.secret_values())
    # No /docs or /openapi.json: nothing about the API is exposed beyond what the UI needs.
    app = FastAPI(title="WikiChat", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityMiddleware, password=settings.secret("access_password"),
                       rate_per_minute=settings.rate_limit_per_minute)
    state: dict = {"index": index, "llm": LLM(settings)}
    logo = org_logo_path(settings)

    def get_index() -> SearchIndex:
        if state["index"] is None:
            try:
                state["index"] = SearchIndex.load(settings)
            except FileNotFoundError as e:
                log.warning("%s", e)
                raise HTTPException(503, "The search index is not built yet. Run: wikichat crawl") from e
        return state["index"]

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    @app.get("/brand/org-logo", include_in_schema=False)
    def org_logo():
        if not logo:
            raise HTTPException(404)
        return FileResponse(logo, media_type=LOGO_TYPES[logo.suffix.lower()])

    @app.get("/api/status")
    def status():
        info = {
            "version": __version__,
            "bot_name": settings.bot_name,
            "org_name": settings.org_name,
            "org_url": settings.org_url,
            "org_logo": "/brand/org-logo" if logo else "",
            "accent_color": settings.accent_color,
            "welcome_title": settings.welcome_title,
            "welcome_text": settings.welcome_text,
            "suggestions": settings.suggestion_list,
            "wiki_url": settings.wiki_url,
            "llm": settings.llm_model if state["llm"].enabled else None,
        }
        try:
            meta = get_index().meta
            info.update({k: meta.get(k) for k in ("pages", "chunks", "built_at")})
        except HTTPException:
            info["no_index"] = True
        return info

    @app.get("/api/search")
    def search(q: str = Query(min_length=1, max_length=500), k: int = Query(8, ge=1, le=20)):
        hits = get_index().search(q, k=k)
        return [{"title": h.chunk.header, "url": h.chunk.url, "text": h.chunk.text, "score": h.score} for h in hits]

    @app.post("/api/chat")
    def chat(req: ChatRequest):
        idx = get_index()
        history = [t.model_dump() for t in req.history]
        hits = idx.search(retrieval_query(req.message, history), k=settings.top_k)

        def gen():
            sources = [
                {"n": n, "title": h.chunk.page_title, "section": h.chunk.section, "url": h.chunk.url,
                 "snippet": h.chunk.text[:280]}
                for n, h in enumerate(hits, 1)
            ]
            yield sse("sources", sources)
            try:
                for token in state["llm"].stream(req.message, hits, history):
                    yield sse("token", token)
            except Exception:  # noqa: BLE001  details go to the log, not to the browser
                log.exception("LLM request failed")
                yield sse("error", "The language model did not answer. Please try again later.")
            yield sse("done", {})

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
