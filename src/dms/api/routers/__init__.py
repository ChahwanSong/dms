"""API routers. Each submodule is imported directly by ``dms.api.app.create_app``.

Deliberately empty of code: this file once held a byte-identical copy of
``dms.api._helpers.storage_mapping`` (secret redaction / PATCH merge) left behind by a
directory move. Nothing imported it. The helpers live in ``.._helpers.storage_mapping``
-- import them from there.

Keep this file: ``pyproject.toml`` discovers packages with ``packages.find``, so removing
``__init__.py`` would drop the whole routers package from the built wheel.
"""
