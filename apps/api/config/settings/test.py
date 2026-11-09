import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-a-secret")

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-only-not-a-secret"
DATABASES["default"]["TEST"] = {"NAME": "test_mira"}  # noqa: F405
OPERATOR_HTTP_ENABLED = True
