"""FastAPI application factory.

* Lifespan: initialise the DB, fail interrupted jobs, start the ingestion
  worker thread.
* Routers are mounted under ``/api`` so the built frontend (``frontend/dist``)
  can be served from the same origin, with SPA fallback for client routes.
* Domain exceptions (``MetricsError`` etc.) are mapped to proper HTTP errors.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .. import ingest
from ..authors import AuthorError
from ..config import PROJECT_ROOT, settings
from ..db import init_db
from ..gitio import GitError
from ..ingest import IngestError
from ..metrics import MetricsError
from .authors_api import router as authors_router
from .metrics_api import router as metrics_router
from .repos import router as repos_router

log = logging.getLogger("rat.api")

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

DESCRIPTION = """
Repo Analysis Tool (RAT) -- measures git repository metrics for authors, files,
directories and whole repositories, per the COMS3011A brief.

Core concepts:

* **H-bar**: non-merge commits reachable from a reference commit (default HEAD).
* **Commit set H**: a time range (``since`` inclusive / ``until`` exclusive), an
  explicit commit list (``commits``), optionally intersected with authors.
* **Scope object o**: the repository root, a directory subtree, or a single file
  (``path`` parameter).

Metric definitions live in the README; every endpoint mirrors the spec sections.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    ingest.mark_interrupted_jobs()
    ingest.start_worker()
    log.info("RAT ready (data_dir=%s)", settings.data_dir)
    yield
    ingest.stop_worker()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Repo Analysis Tool (RAT)",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(repos_router, prefix="/api")
    app.include_router(metrics_router, prefix="/api")
    app.include_router(authors_router, prefix="/api")

    @app.exception_handler(MetricsError)
    async def _metrics_error(_request, exc: MetricsError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(AuthorError)
    async def _author_error(_request, exc: AuthorError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(IngestError)
    async def _ingest_error(_request, exc: IngestError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(GitError)
    async def _git_error(_request, exc: GitError):
        log.warning("git error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "version": app.version}

    # Serve the built SPA when available (production / docker).
    if FRONTEND_DIST.exists():
        from fastapi.staticfiles import StaticFiles

        assets = FRONTEND_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            index = FRONTEND_DIST / "index.html"
            if not index.exists():
                raise HTTPException(status_code=404, detail="Frontend is not built.")
            if full_path:
                candidate = (FRONTEND_DIST / full_path).resolve()
                dist_resolved = FRONTEND_DIST.resolve()
                if candidate.is_file() and dist_resolved in candidate.parents:
                    return FileResponse(candidate)
            return FileResponse(index)

    return app


app = create_app()
