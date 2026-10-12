"""The region app with the scaling hook installed (owner: Simrit).

`region/main.py` belongs to another lane, so the tests start regions through this module instead:
    python -m uvicorn tests.integration.scale_app:app
It is region.main:app plus the single call the joining phase adds to region/main.py:  scale.install(app).
"""
from region.main import app
from region.sync import scale

scale.install(app)
