from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from migration.mapping import MappingError, map_observation, map_patient, patient_fhir_id

LOINC = "http://loinc.org"


def observation(**fields: Any) -> dict[str, Any]:
    return {
        "resourceType": "Observation",
        "id": "obs-1",
        "status": "final",
        "subject": {"reference": "Patient/p-1"},
        "code": {"coding": [{"system": LOINC, "code": "8867-4", "display": "Heart rate"}]},
        **fields,
    }


# --- Patient -----------------------------------------------------------------


def test_patient_prefers_the_official_name_and_keeps_raw() -> None:
    resource = {
        "resourceType": "Patient",
        "id": "p-1",
        "name": [
            {"use": "usual", "family": "Nick"},
            {"use": "official", "family": "Doe", "given": ["Jane", "Q"]},
        ],
        "gender": "female",
        "birthDate": "1970-05-17",
        "meta": {"lastUpdated": "2024-01-02T03:04:05.123+00:00"},
    }

    patient = map_patient(resource)

    assert (patient.fhir_id, patient.family_name, patient.given_names) == ("p-1", "Doe", "Jane Q")
    assert patient.gender == "female"
    assert patient.birth_date == date(1970, 5, 17)
    assert patient.source_updated_at == datetime(2024, 1, 2, 3, 4, 5, 123000, tzinfo=UTC)
    assert patient.raw is resource


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ([{"use": "nickname", "family": "A"}, {"use": "usual", "family": "B"}], ("B", "")),
        ([{"family": "First"}, {"family": "Second"}], ("First", "")),
        ([{"text": "Jane Doe"}], ("Jane Doe", "")),
        ([], ("", "")),
    ],
)
def test_patient_name_fallbacks(names: list[dict[str, Any]], expected: tuple[str, str]) -> None:
    patient = map_patient({"id": "p-1", "name": names})

    assert (patient.family_name, patient.given_names) == expected


@pytest.mark.parametrize("birth_date", ["1970", "1970-05", None])
def test_partial_or_missing_birth_date_is_not_invented(birth_date: str | None) -> None:
    assert map_patient({"id": "p-1", "birthDate": birth_date}).birth_date is None


def test_unknown_gender_is_left_blank() -> None:
    assert map_patient({"id": "p-1", "gender": "robot"}).gender == ""


def test_resource_without_id_is_rejected() -> None:
    with pytest.raises(MappingError):
        map_patient({"resourceType": "Patient"})


# --- Observation: subject ----------------------------------------------------


@pytest.mark.parametrize(
    "reference",
    ["Patient/p-1", "https://fhir.test/baseR4/Patient/p-1", "Patient/p-1/_history/3"],
)
def test_subject_reference_forms_are_normalised(reference: str) -> None:
    assert patient_fhir_id({"subject": {"reference": reference}}) == "p-1"


@pytest.mark.parametrize("subject", [{"reference": "Group/g-1"}, {"display": "no ref"}, None])
def test_subject_that_is_not_a_patient_is_rejected(subject: dict[str, str] | None) -> None:
    with pytest.raises(MappingError):
        map_observation(observation(subject=subject))


# --- Observation: code, category, dates --------------------------------------


def test_loinc_coding_is_preferred_and_category_is_mapped() -> None:
    _, obs = map_observation(
        observation(
            code={
                "coding": [
                    {"system": "http://snomed.info/sct", "code": "364075005"},
                    {"system": LOINC, "code": "8867-4", "display": "Heart rate"},
                ]
            },
            category=[{"coding": [{"code": "vital-signs"}]}],
        )
    )

    assert (obs.code_system, obs.code, obs.display) == (LOINC, "8867-4", "Heart rate")
    assert obs.category == "vital-signs"


def test_display_falls_back_to_code_text() -> None:
    _, obs = map_observation(observation(code={"coding": [{"code": "x"}], "text": "Free text"}))

    assert obs.display == "Free text"


TIMESTAMP = datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)
MIDNIGHT = datetime(2020, 1, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"effectiveDateTime": "2020-01-02T03:04:05Z"}, (TIMESTAMP, False)),
        ({"effectiveInstant": "2020-01-02T03:04:05+00:00"}, (TIMESTAMP, False)),
        ({"effectivePeriod": {"start": "2020-01-02T03:04:05Z"}}, (TIMESTAMP, False)),
        # Day precision is real FHIR data (2.5% of the sandbox): kept, flagged as date-only.
        ({"effectiveDateTime": "2020-01-02"}, (MIDNIGHT, True)),
        # Partial date or timestamp without timezone: nothing reliable to keep.
        ({"effectiveDateTime": "2020-01"}, (None, False)),
        ({"effectiveDateTime": "2020-01-02T03:04:05"}, (None, False)),
        ({}, (None, False)),
    ],
)
def test_effective_date(fields: dict[str, Any], expected: tuple[datetime | None, bool]) -> None:
    _, obs = map_observation(observation(**fields))

    assert (obs.effective_at, obs.effective_date_only) == expected


# --- Observation: the value shapes found on the sandbox ----------------------


def test_quantity_keeps_the_exact_decimal() -> None:
    _, obs = map_observation(
        observation(valueQuantity={"value": 98.39, "unit": "mg/dL", "code": "mg/dL"})
    )

    assert obs.value_type == "quantity"
    assert obs.value_number == Decimal("98.39")
    assert obs.value_unit == "mg/dL"


def test_quantity_unit_falls_back_to_code() -> None:
    _, obs = map_observation(observation(valueQuantity={"value": 72, "code": "/min"}))

    assert (obs.value_number, obs.value_unit) == (Decimal("72"), "/min")


def test_codeable_concept_value() -> None:
    _, obs = map_observation(
        observation(valueCodeableConcept={"coding": [{"display": "Never smoker"}], "text": "x"})
    )

    assert (obs.value_type, obs.value_text) == ("concept", "Never smoker")


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"valueString": "Clear"}, "Clear"),
        ({"valueInteger": 3}, "3"),
        ({"valueBoolean": False}, "false"),
    ],
)
def test_scalar_values_become_text(fields: dict[str, Any], expected: str) -> None:
    _, obs = map_observation(observation(**fields))

    assert (obs.value_type, obs.value_text) == ("string", expected)


def test_blood_pressure_components() -> None:
    def component(code: str, display: str, value: float) -> dict[str, Any]:
        return {
            "code": {"coding": [{"system": LOINC, "code": code, "display": display}]},
            "valueQuantity": {"value": value, "unit": "mm[Hg]"},
        }

    _, obs = map_observation(
        observation(
            component=[component("8480-6", "Systolic", 120.0), component("8462-4", "Diastolic", 80)]
        )
    )

    assert obs.value_type == "components"
    assert obs.components == [
        {"code": "8480-6", "display": "Systolic", "value": "120", "unit": "mm[Hg]"},
        {"code": "8462-4", "display": "Diastolic", "value": "80", "unit": "mm[Hg]"},
    ]


def test_observation_without_value() -> None:
    _, obs = map_observation(observation())

    assert obs.value_type == "none"
    assert obs.value_number is None


@pytest.mark.parametrize("value", ["12", True, 1e30])
def test_invalid_quantity_is_rejected(value: Any) -> None:
    with pytest.raises(MappingError):
        map_observation(observation(valueQuantity={"value": value}))
