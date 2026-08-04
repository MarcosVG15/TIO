"""TIO HTTP API.

Local:       uvicorn api:app --reload      ->  http://localhost:8000/api/...
Production:  behind Caddy on tio.agency    ->  https://tio.agency/api/...

Everything lives under /api, so the frontend calls relative paths and the
browser never crosses an origin. There is no CORS configuration because
there is no cross-origin request to allow.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

import auth as auth_config
from routers import auth, onboarding, profile, trips


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

api = APIRouter(prefix="/api")
api.include_router(auth.router)
api.include_router(onboarding.router)
api.include_router(profile.router)
api.include_router(trips.router)
app.include_router(api)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe for Docker and uptime monitors. Unauthenticated."""
    return {"status": "ok"}
