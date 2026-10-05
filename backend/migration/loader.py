"""Import orchestration: one batch = one page of patients + all their observations."""

import logging
from collections.abc import Callable
from typing import Any

from django.db import transaction

from migration.fhir_client import FhirClient, FhirClientError
from migration.mapping import MappingError, map_observation, map_patient
from migration.models import MigrationRun, Observation, Patient

logger = logging.getLogger(__name__)

BATCH_SIZE = 500  # HAPI caps _count at 500


def run_import(fhir: FhirClient, *, limit: int | None = None) -> MigrationRun:
    """Import patients and their observations batch by batch. Safe to re-run."""
    run = MigrationRun.objects.create()
    seen = 0
    try:
        page_size = min(BATCH_SIZE, limit) if limit else BATCH_SIZE
        for patient_page in fhir.search("Patient", {"_count": page_size}):
            if limit is not None:
                patient_page = patient_page[: limit - seen]
            seen += len(patient_page)
            _import_batch(fhir, patient_page, run)
            _record_http_stats(run, fhir)
            run.save()  # checkpoint: one UPDATE per batch
            if limit is not None and seen >= limit:
                break
    except BaseException as exc:  # includes Ctrl+C: a run never stays "running"
        _record_http_stats(run, fhir)
        run.finish(MigrationRun.Status.FAILED, error=_safe_error(exc))
        raise
    run.finish(MigrationRun.Status.SUCCEEDED)
    return run


def _import_batch(fhir: FhirClient, patient_resources: list[Any], run: MigrationRun) -> None:
    patients = _map_all(map_patient, patient_resources, run)
    if not patients:
        return
    subjects = ",".join(f"Patient/{patient.fhir_id}" for patient in patients)

    # Network first, database second: no transaction is held open during HTTP calls.
    observation_resources = [
        resource
        for page in fhir.search(
            "Observation", {"subject": subjects, "_count": BATCH_SIZE}, post=True
        )
        for resource in page
    ]
    source_count = fhir.count("Observation", {"subject": subjects})
    mapped = _map_all(map_observation, observation_resources, run)

    # The whole batch is written atomically: patients never land without their observations.
    with transaction.atomic():
        patient_pks = _upsert_patients(patients)
        observations = []
        for patient_fhir_id, observation in mapped:
            patient_pk = patient_pks.get(patient_fhir_id)
            if patient_pk is None:
                run.skipped_orphans += 1
                continue
            observation.patient_id = patient_pk
            observations.append(observation)
        _upsert(Observation, observations)

    run.batches += 1
    run.patients_written += len(patient_pks)
    run.observations_written += len(observations)
    if source_count != len(observation_resources):
        # A warning, not a failure: the public sandbox changes while we read it.
        run.count_mismatches += 1
        logger.warning(
            "Batch %d: source counts %d observations, %d fetched",
            run.batches,
            source_count,
            len(observation_resources),
        )
    logger.info(
        "Batch %d: %d patients, %d observations",
        run.batches,
        len(patient_pks),
        len(observations),
    )


def _map_all[T](mapper: Callable[[Any], T], resources: list[Any], run: MigrationRun) -> list[T]:
    mapped = []
    for resource in resources:
        try:
            mapped.append(mapper(resource))
        except Exception as exc:  # one malformed resource never fails its batch
            run.skipped_invalid += 1
            fhir_id = resource.get("id") if isinstance(resource, dict) else None
            # Our own messages are PHI-free; an unexpected error could quote a field value.
            reason = str(exc) if isinstance(exc, MappingError) else type(exc).__name__
            logger.warning("Skipped resource %s: %s", fhir_id, reason)
    return mapped


def _upsert_patients(patients: list[Patient]) -> dict[str, int]:
    _upsert(Patient, patients)
    fhir_ids = [patient.fhir_id for patient in patients]
    return dict(Patient.objects.filter(fhir_id__in=fhir_ids).values_list("fhir_id", "pk"))


def _upsert(model: type[Patient] | type[Observation], rows: list[Any]) -> None:
    # Last occurrence wins if the source returned the same resource twice.
    unique_rows = list({row.fhir_id: row for row in rows}.values())
    update_fields = [
        field.name for field in model._meta.concrete_fields if field.name not in ("id", "fhir_id")
    ]
    model.objects.bulk_create(
        unique_rows, update_conflicts=True, unique_fields=["fhir_id"], update_fields=update_fields
    )


def _record_http_stats(run: MigrationRun, fhir: FhirClient) -> None:
    run.http_requests = fhir.stats.requests
    run.http_retries = fhir.stats.retries
    run.bytes_received = fhir.stats.bytes_received


def _safe_error(exc: BaseException) -> str:
    # Client messages are built to be PHI-free; anything else is reduced to its type.
    return str(exc) if isinstance(exc, FhirClientError) else type(exc).__name__
