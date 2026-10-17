"""Settings used by pytest only (see pyproject.toml).

pytest-django configures Django before any conftest.py is imported, and the real settings
refuse to load without DJANGO_SECRET_KEY. This module supplies a throwaway key first.
It is not used by manage.py or by the WSGI entry point.
"""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-for-deployment")

from config.settings import *  # noqa: F403
