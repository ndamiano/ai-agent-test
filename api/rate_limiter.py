"""Rate limiter configuration for the API."""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Initialize rate limiter (no rate limiting)
limiter = Limiter(key_func=get_remote_address)
