import pytest
from django.core.management import call_command


def test_system_checks_pass() -> None:
    call_command("check", fail_level="WARNING")


@pytest.mark.django_db  # makemigrations reads the applied-migrations history
def test_models_and_migrations_are_in_sync() -> None:
    # Fails (SystemExit) if a model change was committed without its migration.
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
