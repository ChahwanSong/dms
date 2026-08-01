"""DMS HTTP API package.

Public entry point: :func:`create_app`.

Routers, request models, helpers, and dependencies are organized in submodules.
External callers should typically only import :func:`create_app` from this
package; everything else is considered internal and may move between modules.
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
