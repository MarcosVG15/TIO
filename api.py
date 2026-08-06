"""TIO HTTP API.

Local:       uvicorn api:app --reload      ->  http://localhost:8000/api/...
Production:  behind Caddy on tio.agency    ->  https://tio.agency/api/...

Everything lives under /api, so the frontend calls relative paths and the
browser never crosses an origin. There is no CORS configuration because
there is no cross-origin request to allow.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import auth as auth_config
from routers import (
    auth,
    conversations,
    discovery,
    drafts,
    flights,
    friends,
    onboarding,
    planner,
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


def _warm_destinations() -> None:
    """Compute the destination shelf once, off any request path.

    The shelf's last-good fallback lives in process memory, so every restart
    starts with nothing to fall back to - and the first visitor after a deploy
    is exactly who meets a database busy with corpus enrichment. Warming it in
    the background means that visitor gets a shelf rather than an apology, and
    nobody ever waits for the first computation.

    Best effort by design. A failure here must not stop the API from serving
    everything else, so it is logged and dropped; the next request simply falls
    through to computing it itself.
    """
    import recommend

    try:
        found = recommend.top_destinations(days=4, limit=25)
        logging.getLogger(__name__).info(
            "warmed destination shelf: %d cities", len(found)
        )
    except Exception as exc:  # noqa: BLE001 - never fatal at boot
        logging.getLogger(__name__).warning(
            "could not warm destination shelf: %s", exc
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start on bad configuration. A boot failure in the deploy log
    # beats a 500 the first time a real user tries to sign up.
    auth_config.validate_config()

    # After validation, before serving: a daemon thread so a slow aggregate
    # cannot delay the port opening and fail the container's health check.
    threading.Thread(target=_warm_destinations, daemon=True).start()
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
# Before trips: both routers own paths under /trips, and FastAPI matches in
# registration order. With trips first, "/trips/drafts" is swallowed by
# "/trips/{trip_id}" and the drafts screen gets "trip not found".
api.include_router(drafts.router)
api.include_router(trips.router)
api.include_router(conversations.router)
api.include_router(social.router)
api.include_router(discovery.router)
api.include_router(friends.router)
api.include_router(uploads.router)
api.include_router(planner.router)
api.include_router(flights.router)
app.include_router(api)


#: Field names the screens use, as a person would say them.
_FIELD_LABELS = {
    "destination": "destination",
    "start_date": "start date",
    "end_date": "end date",
    "travellers": "number of travellers",
    "budget": "budget",
    "currency": "currency",
    "title": "trip name",
    "prompt": "trip description",
    "email": "email address",
    "password": "password",
    "name": "name",
}


@app.exception_handler(RequestValidationError)
async def readable_validation_error(request, exc: RequestValidationError):
    """Turn Pydantic's error list into one sentence the screen can render.

    FastAPI's default `detail` is a list of error objects. Every screen in this
    app renders `detail` as a string, so a list renders as nothing - and the
    user is shown the generic network message instead. A rejected date then
    reads as "Could not connect to the server", which sends everyone looking
    at the wrong layer. It cost most of an afternoon once already.

    The original list is kept under `errors` for anyone debugging; only the
    human-facing field changes shape.
    """
    parts: list[str] = []
    for error in exc.errors():
        location = [str(p) for p in error.get("loc", []) if p != "body"]
        field = location[-1] if location else "request"
        label = _FIELD_LABELS.get(field, field.replace("_", " "))
        message = error.get("msg", "is not valid")
        if error.get("type") == "missing":
            parts.append(f"{label} is required")
        else:
            parts.append(f"{label}: {message}")

    detail = "; ".join(parts[:3]) or "Some of that could not be understood."
    logging.getLogger(__name__).info(
        "422 on %s %s - %s", request.method, request.url.path, detail
    )
    return JSONResponse(
        status_code=422,
        content={"detail": detail[:400], "errors": jsonable_encoder(exc.errors())},
    )


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe for Docker and uptime monitors. Unauthenticated."""
    return {"status": "ok"}
