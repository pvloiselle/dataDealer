"""
extensions.py — Flask extension instances
──────────────────────────────────────────
Defined here (not in app.py) so that modules like dashboard.py can import them
without creating a circular import with app.py.
"""

from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

csrf    = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
