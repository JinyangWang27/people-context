"""Loopback browser adapter for `pctx browse`."""

from people_context.adapters.web.app import LOOPBACK_HOST, TOKEN_HEADER, create_browse_app

__all__ = ["LOOPBACK_HOST", "TOKEN_HEADER", "create_browse_app"]
