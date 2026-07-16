import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import get_settings, validate_settings_for_production
from app.routers.admin_routes import router as admin_router
from app.routers.alumni_routes import router as alumni_router
from app.routers.analytics_routes import router as analytics_router
from app.routers.auth_routes import limiter, router as auth_router
from app.routers.content_routes import router as content_router
from app.routers.profile_routes import router as profile_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()
validate_settings_for_production(settings)

app = FastAPI(
    title="Alumni Dashboard API",
    description=(
        "Multi-organization authentication and alumni network data API "
        "(FSU CCI, FSU STARS, STARS National) for the alumni dashboard frontend."
    ),
    version="2.0.0",
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)

# --- Rate limiting (protects /login from brute-force attempts) ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- CORS: only the configured frontend origin(s) may call this API ---
cors_origins = settings.cors_origins_list
if not cors_origins:
    logger.warning(
        "ALLOWED_ORIGINS is not set. No cross-origin requests from a "
        "browser frontend will be permitted until it is configured."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error while processing %s %s", request.method, request.url.path)
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "Internal server error"})


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(alumni_router)
app.include_router(analytics_router)
app.include_router(admin_router)
app.include_router(content_router)
app.include_router(profile_router)

# Serve uploaded profile photos (local storage provider only). Only files
# saved through app.services.storage_service (server-generated filenames)
# live under this directory - nothing else is ever written here.
settings.uploads_dir_full_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(settings.uploads_dir_full_path)), name="uploads")
