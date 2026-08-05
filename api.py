"""TIO HTTP API.

Local:       uvicorn api:app --reload      ->  http://localhost:8000/api/...
Production:  behind Caddy on tio.agency    ->  https://tio.agency/api/...

Everything lives under /api, so the frontend calls relative paths and the
browser never crosses an origin. There is no CORS configuration because
there is no cross-origin request to allow.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import auth as auth_config
from routers import (
    auth,
    conversations,
    discovery,
    friends,
    onboarding,
    profile,
    social,
    trips,
    uploads,
)


def _dev_origins() -> list[str]:
    """Origins allowed to call the API cross-origin.

    Production does not need this: tio.agency serves the frontend and the API
    from the same origin, so the browser never does a CORS check. It exists
    only for external dev previews (Lovable, a local Vite server) which live
    on a different domain. Empty by default - nothing is permitted unless
    explicitly listed.
    """
    raw = os.getenv("ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start on bad configuration. A boot failure in the deploy log
    # beats a 500 the first time a real user tries to sign up.
    auth_config.validate_config()
    yield


app = FastAPI(
    title="TIO",
    version="0.1.0",
    description="Travel planning backend. All endpoints are under /api.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

if _dev_origins():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_dev_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

api = APIRouter(prefix="/api")
api.include_router(auth.router)
api.include_router(onboarding.router)
api.include_router(profile.router)
api.include_router(trips.router)
api.include_router(conversations.router)
api.include_router(social.router)
api.include_router(discovery.router)
api.include_router(friends.router)
api.include_router(uploads.router)
app.include_router(api)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe for Docker and uptime monitors. Unauthenticated."""
    return {"status": "ok"}
