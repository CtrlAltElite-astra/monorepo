"""The region app, as the scale tests start it (owner: Simrit).

    python -m uvicorn tests.integration.scale_app:app

region/main.py now calls `scale.install(app)` itself (joining step J0), so this module only re-exports the app.
Calling install a second time would count every write twice and halve the region limit.
"""
from region.main import app  # noqa: F401
