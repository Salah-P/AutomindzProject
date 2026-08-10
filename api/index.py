"""Vercel Python entrypoint — re-export the FastAPI app."""

from main import app

__all__ = ["app"]
