from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from pytest_django import DjangoAssertNumQueries

from migration.models import Observation, Patient

pytestmark = pytest.mark.django_db

QUANTITY = Observation.ValueType.QUANTITY


def make_patient(fhir_id: str, family: str, given: str = "") -> Patient:
    return Patient.objects.create(
        fhir_id=fhir_id, family_name=family, given_names=given, raw={"secret": "source"}
    )


def make_observation(patient: Patient, fhir_id: str, **fields: Any) -> Observation:
    defaults: dict[str, Any] = {"value_type": QUANTITY, "raw": {"secret": "source"}}
    return Observation.objects.create(patient=patient, fhir_id=fhir_id, **{**defaults, **fields})


def day(n: int) -> datetime:
    return datetime(2024, 1, n, tzinfo=UTC)


def test_list_is_sorted_by_name_with_counts_in_one_query(
    client: Client, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    zed, ada = make_patient("p-z", "Zed", "Zoe"), make_patient("p-a", "Ada", "Lovelace")
    make_observation(ada, "o-1", effective_at=day(1))
    make_observation(ada, "o-2", effective_at=day(9))
    make_observation(zed, "o-3")

    with django_assert_num_queries(1):
        response = client.get("/api/patients/")

    assert response.status_code == 200
    body = response.json()
    assert [p["name"] for p in body] == ["Lovelace Ada", "Zoe Zed"]
    assert body[0]["observation_count"] == 2
    assert body[0]["last_observation_at"] == "2024-01-09T00:00:00Z"
    assert (body[1]["observation_count"], body[1]["last_observation_at"]) == (1, None)


def test_detail_returns_observations_most_recent_first_in_two_queries(
    client: Client, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    patient = make_patient("p-1", "Doe", "Jane")
    make_observation(patient, "o-old", effective_at=day(1))
    make_observation(patient, "o-undated")
    make_observation(patient, "o-new", effective_at=day(5))

    with django_assert_num_queries(2):
        response = client.get(f"/api/patients/{patient.pk}/")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Jane Doe"
    assert [o["fhir_id"] for o in body["observations"]] == ["o-new", "o-old", "o-undated"]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"value_number": Decimal("98.390000"), "value_unit": "mg/dL"}, "98.39 mg/dL"),
        ({"value_type": "concept", "value_text": "Never smoker"}, "Never smoker"),
        (
            {
                "value_type": "components",
                "components": [
                    {"code": "8480-6", "display": "Systolic", "value": "120", "unit": "mm[Hg]"},
                    {"code": "8462-4", "display": "Diastolic", "value": "80", "unit": "mm[Hg]"},
                ],
            },
            "120/80 mm[Hg]",
        ),
        ({"value_type": "none"}, ""),
    ],
)
def test_observation_value_is_ready_to_display(
    client: Client, fields: dict[str, Any], expected: str
) -> None:
    patient = make_patient("p-1", "Doe")
    make_observation(patient, "o-1", **fields)

    observation = client.get(f"/api/patients/{patient.pk}/").json()["observations"][0]

    assert observation["value"] == expected


def test_source_resource_is_never_exposed(client: Client) -> None:
    patient = make_patient("p-1", "Doe")
    make_observation(patient, "o-1")

    responses = [client.get("/api/patients/"), client.get(f"/api/patients/{patient.pk}/")]

    assert all(b"secret" not in response.content for response in responses)


@pytest.mark.parametrize("path", ["/api/patients/999/", "/api/patients/not-a-number/"])
def test_unknown_patient_is_a_404(client: Client, path: str) -> None:
    assert client.get(path).status_code == 404


def test_api_is_read_only(client: Client) -> None:
    response = client.post("/api/patients/", {}, content_type="application/json")

    assert response.status_code == 405


def test_list_is_gzipped_when_the_client_accepts_it(client: Client) -> None:
    for i in range(20):
        make_patient(f"p-{i}", f"Family{i}")

    response = client.get("/api/patients/", headers={"Accept-Encoding": "gzip"})

    assert response.headers["Content-Encoding"] == "gzip"
