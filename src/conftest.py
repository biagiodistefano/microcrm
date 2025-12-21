"""Global test fixtures."""

import typing as t

import pytest


@pytest.fixture(autouse=True)
def configure_test_database(settings: t.Any) -> None:
    """Configure tests to use in-memory SQLite database."""
    settings.DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }


@pytest.fixture(autouse=True)
def enable_celery_eager_mode(settings: t.Any) -> None:
    """Enable Celery eager mode for tests so tasks execute synchronously."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture(autouse=True)
def use_fast_password_hasher(settings: t.Any) -> None:
    """Use MD5 password hasher for faster user creation in tests."""
    settings.PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.MD5PasswordHasher",
    ]


@pytest.fixture(autouse=True)
def disable_api_auth(settings: t.Any) -> None:
    """Disable API authentication in tests."""
    settings.DEBUG = True  # API auth is disabled when DEBUG=True
