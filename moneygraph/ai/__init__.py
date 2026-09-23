"""Optional explanation module. Import only when the API enables the AI feature."""
from .config import AIConfig
from .context import ClientContext, SnapshotContextBuilder
from .errors import AIUnavailable, InvalidContext
from .service import ExplanationService, explain_client

__all__ = ["AIConfig", "AIUnavailable", "ClientContext", "ExplanationService",
           "InvalidContext", "SnapshotContextBuilder", "explain_client"]
