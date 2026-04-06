"""Rate limiter configuration for the API."""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Initialize rate limiter (2 requests per second)
limiter = Limiter(key_func=get_remote_address, default_limits=["2/second"])
