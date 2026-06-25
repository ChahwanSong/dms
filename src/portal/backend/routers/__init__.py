"""Role-scoped API routers for the portal BFF.

The portal's two interfaces are separated at the API layer too: ``user_router``
(end users) and ``operator_router`` (operators/admins) are each gated by role via
``security.require_role``. Each interface's SPA talks only to its own surface.
"""

from .operator import operator_router
from .user import user_router

__all__ = ["operator_router", "user_router"]
