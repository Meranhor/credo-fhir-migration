import argparse
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from migration.fhir_client import FhirClient, FhirClientError
from migration.loader import run_import
from migration.models import MigrationRun


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


class Command(BaseCommand):
    help = "Import Patients and their Observations from a FHIR R4 server. Safe to re-run."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--limit", type=positive_int, help="Stop after N patients.")
        parser.add_argument("--base-url", default=settings.FHIR_BASE_URL)

    def handle(self, *args: Any, limit: int | None, base_url: str, **options: Any) -> None:
        self.stdout.write(f"Importing from {base_url}" + (f" (limit {limit})" if limit else ""))
        try:
            with FhirClient(base_url) as fhir:
                run = run_import(fhir, limit=limit)
        except FhirClientError as exc:
            raise CommandError(f"Import failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(summary(run)))


def summary(run: MigrationRun) -> str:
    assert run.finished_at is not None
    seconds = (run.finished_at - run.started_at).total_seconds()
    checked = run.batches - run.count_mismatches
    rows = [
        ("Patients written", f"{run.patients_written:,}"),
        ("Observations written", f"{run.observations_written:,}"),
        ("Skipped (invalid / orphan)", f"{run.skipped_invalid} / {run.skipped_orphans}"),
        ("Batches matching source count", f"{checked}/{run.batches}"),
        ("HTTP requests (retries)", f"{run.http_requests} ({run.http_retries})"),
        ("Data received (compressed)", f"{run.bytes_received / 1024**2:.1f} MiB"),
        ("Duration", f"{seconds:.1f} s"),
    ]
    width = max(len(label) for label, _ in rows)
    lines = [f"Migration run #{run.pk} {run.status}"]
    lines += [f"  {label:<{width}}  {value}" for label, value in rows]
    return "\n".join(lines)
