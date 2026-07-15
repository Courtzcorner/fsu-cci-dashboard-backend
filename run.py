"""
Local development entrypoint.

Usage:
    python run.py

For production, use gunicorn/uvicorn workers instead (see README.md / Procfile).
"""
import os

import uvicorn

if __name__ == "__main__":
    reload_enabled = os.getenv("ENVIRONMENT", "development").lower() != "production"
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=reload_enabled,
    )
