"""Read-only investigation API. Does not calculate roles or risk scores."""

from .app import create_app

__all__ = ["create_app"]
