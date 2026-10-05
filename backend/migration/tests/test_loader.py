from collections.abc import Iterator
from io import StringIO
from typing import Any

import httpx
import pytest
import respx
from django.core.management import call_command
from django.core.management.base import CommandError

from migration.fhir_client import FhirClient, FhirClientError
from migration.loader import run_import
from migration.models import MigrationRun, Observation, Patient

BASE = "https://fhir.test/baseR4"
pytestmark = pytest.mark.django_db


def searchset(resources: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [{"resource": r} for r in resources],
        **extra,
    }


def patient(fhir_id: str) -> dict[str, Any]:
    return {"resourceType": "Patient", "id": fhir_id, "name": [{"family": f"Fam-{fhir_id}"}]}


def obs(fhir_id: str, patient_id: str) -> dict[str, Any]:
    return {
        "resourceType": "Observation",
        "id": fhir_id,
        "status": "final",
        "subject": {"reference": f"Patient/{patient_id}"},
        "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
        "valueQuantity": {"value": 72, "unit": "/min"},
    }


OBSERVATIONS = [obs("o-1", "p-1"), obs("o-2", "p-1"), obs("o-3", "p-2")]


@pytest.fixture
def fhir() -> Iterator[FhirClient]:
    with FhirClient(BASE, sleep=lambda _: None) as client:
        yield client


def mock_source(
    respx_mock: respx.MockRouter,
    observations: list[dict[str, Any]] = OBSERVATIONS,
    source_count: int | None = None,
) -> respx.Route:
    respx_mock.get(f"{BASE}/Patient").respond(json=searchset([patient("p-1"), patient("p-2")]))
    total = len(observations) if source_count is None else source_count
    # Same endpoint for the search and the count: tell them apart by the form body.
    return respx_mock.post(f"{BASE}/Observation/_search").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=searchset([], total=total)
            if b"_summary=count" in request.content
            else searchset(observations),
        )
    )


def test_import_writes_patients_and_observations(
    fhir: FhirClient, respx_mock: respx.MockRouter
) -> None:
    mock_source(respx_mock)

    run = run_import(fhir)

    assert run.status == MigrationRun.Status.SUCCEEDED
    assert (run.batches, run.patients_written, run.observations_written) == (1, 2, 3)
    assert run.http_requests == 3  # patients page + observations page + count
    assert Patient.objects.get(fhir_id="p-1").observations.count() == 2
    assert Observation.objects.get(fhir_id="o-3").patient.fhir_id == "p-2"


def test_import_is_idempotent(fhir: FhirClient, respx_mock: respx.MockRouter) -> None:
    mock_source(respx_mock)

    run_import(fhir)
    run_import(fhir)

    assert (Patient.objects.count(), Observation.objects.count()) == (2, 3)


def test_failed_batch_leaves_nothing_behind(fhir: FhirClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{BASE}/Patient").respond(json=searchset([patient("p-1")]))
    respx_mock.post(f"{BASE}/Observation/_search").respond(503)

    with pytest.raises(FhirClientError):
        run_import(fhir)

    # Patients are not written without their observations.
    assert Patient.objects.count() == 0
    run = MigrationRun.objects.get()
    assert run.status == MigrationRun.Status.FAILED
    assert "HTTP 503 after 5 attempts" in run.error
    assert run.http_retries == 4


def test_invalid_and_orphan_observations_are_counted_not_fatal(
    fhir: FhirClient, respx_mock: respx.MockRouter
) -> None:
    broken = {**obs("o-bad", "p-1"), "valueQuantity": {"value": "not a number"}}
    stranger = obs("o-orphan", "p-unknown")
    mock_source(respx_mock, observations=[*OBSERVATIONS, broken, stranger])

    run = run_import(fhir)

    assert run.status == MigrationRun.Status.SUCCEEDED
    assert (run.skipped_invalid, run.skipped_orphans, run.observations_written) == (1, 1, 3)


def test_count_mismatch_is_reported_as_a_warning(
    fhir: FhirClient, respx_mock: respx.MockRouter
) -> None:
    mock_source(respx_mock, source_count=4)

    run = run_import(fhir)

    assert run.status == MigrationRun.Status.SUCCEEDED
    assert run.count_mismatches == 1


def test_limit_asks_the_server_for_fewer_patients(
    fhir: FhirClient, respx_mock: respx.MockRouter
) -> None:
    patients = respx_mock.get(f"{BASE}/Patient", params={"_count": "1"}).respond(
        json=searchset([patient("p-1")], link=[{"relation": "next", "url": f"{BASE}?page=2"}])
    )
    observation_route = respx_mock.post(f"{BASE}/Observation/_search").mock(
        side_effect=lambda request: httpx.Response(200, json=searchset([], total=0))
    )

    run = run_import(fhir, limit=1)

    assert patients.call_count == 1  # the next page is never requested
    assert observation_route.call_count == 2
    assert run.patients_written == 1


def test_command_prints_a_summary(respx_mock: respx.MockRouter) -> None:
    mock_source(respx_mock)
    out = StringIO()

    call_command("import_fhir", "--base-url", BASE, stdout=out)

    assert "succeeded" in out.getvalue()
    assert "Observations written" in out.getvalue()


def test_command_turns_source_failures_into_a_command_error(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(f"{BASE}/Patient").respond(404)

    with pytest.raises(CommandError, match="HTTP 404"):
        call_command("import_fhir", "--base-url", BASE, stdout=StringIO())
