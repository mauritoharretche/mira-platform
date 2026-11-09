from .base import *  # noqa: F403

DEBUG = True

# Development remains bound to loopback; never carry this exception to deployment.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
