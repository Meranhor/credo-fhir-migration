"""Pure functions: one FHIR resource in, one unsaved model instance out. No I/O."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from migration.models import Observation, Patient, format_decimal

Resource = dict[str, Any]

LOINC = "http://loinc.org"
NAME_USE_PREFERENCE = ("official", "usual")
FULL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Relative, absolute or versioned: "Patient/1", "https://x/fhir/Patient/1", "Patient/1/_history/2".
PATIENT_REFERENCE = re.compile(r"^(?:.*/)?Patient/([A-Za-z0-9.\-]{1,64})(?:/_history/[^/]+)?$")
SIX_PLACES = Decimal("0.000001")
MAX_INTEGER_DIGITS = 14  # value_number is DECIMAL(20, 6)


class MappingError(ValueError):
    """The resource cannot be mapped; it is skipped and counted, never fatal."""


def map_patient(resource: Resource) -> Patient:
    family, given = _name(resource.get("name") or [])
    gender = resource.get("gender")
    return Patient(
        fhir_id=_id(resource),
        family_name=family,
        given_names=given,
        gender=gender if gender in Patient.Gender.values else "",
        birth_date=_full_date(resource.get("birthDate")),
        source_updated_at=_last_updated(resource),
        raw=resource,
    )


def map_observation(resource: Resource) -> tuple[str, Observation]:
    """Return the subject's patient FHIR id and the observation (patient not set yet)."""
    coding = _preferred_coding(resource.get("code") or {})
    observation = Observation(
        fhir_id=_id(resource),
        status=resource.get("status") or "",
        category=_category(resource.get("category") or []),
        code_system=coding.get("system") or "",
        code=coding.get("code") or "",
        display=coding.get("display") or (resource.get("code") or {}).get("text") or "",
        source_updated_at=_last_updated(resource),
        raw=resource,
    )
    observation.effective_at, observation.effective_date_only = _effective(resource)
    _set_value(observation, resource)
    return patient_fhir_id(resource), observation


def patient_fhir_id(observation: Resource) -> str:
    reference = (observation.get("subject") or {}).get("reference") or ""
    match = PATIENT_REFERENCE.match(reference)
    if match is None:
        raise MappingError("subject is not a Patient reference")
    return match.group(1)


def _id(resource: Resource) -> str:
    fhir_id = resource.get("id")
    if not isinstance(fhir_id, str) or not fhir_id:
        raise MappingError("resource has no id")
    return fhir_id


def _name(names: list[Resource]) -> tuple[str, str]:
    if not names:
        return "", ""
    by_use = {name.get("use"): name for name in reversed(names)}  # first occurrence wins
    name = next((by_use[use] for use in NAME_USE_PREFERENCE if use in by_use), names[0])
    family = name.get("family") or ""
    given = " ".join(name.get("given") or [])
    if not family and not given:
        return name.get("text") or "", ""
    return family, given


def _full_date(value: Any) -> date | None:
    # Partial dates ("1970", "1970-05") are not invented into a full date.
    if isinstance(value, str) and FULL_DATE.match(value):
        return date.fromisoformat(value)
    return None


def _datetime(value: Any) -> datetime | None:
    # Only a full timestamp with a timezone; partial values stay null.
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _last_updated(resource: Resource) -> datetime | None:
    return _datetime((resource.get("meta") or {}).get("lastUpdated"))


def _effective(resource: Resource) -> tuple[datetime | None, bool]:
    """Timestamp and whether the source only gave a day (stored at 00:00 UTC)."""
    for value in (
        resource.get("effectiveDateTime"),
        resource.get("effectiveInstant"),
        (resource.get("effectivePeriod") or {}).get("start"),
    ):
        if value is not None:
            day = _full_date(value)
            if day is not None:
                return datetime(day.year, day.month, day.day, tzinfo=UTC), True
            return _datetime(value), False
    return None, False


def _category(categories: list[Resource]) -> str:
    for category in categories:
        for coding in category.get("coding") or []:
            if coding.get("code"):
                return str(coding["code"])
    return ""


def _preferred_coding(concept: Resource) -> Resource:
    codings: list[Resource] = concept.get("coding") or []
    return next((c for c in codings if c.get("system") == LOINC), codings[0] if codings else {})


def _concept_text(concept: Resource) -> str:
    codings = concept.get("coding") or []
    return next((c["display"] for c in codings if c.get("display")), concept.get("text") or "")


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MappingError("quantity value is not a number")
    try:
        # str(float) is the shortest repr that round-trips: 98.39 stays 98.39.
        number = Decimal(str(value)).quantize(SIX_PLACES)
    except InvalidOperation as exc:
        raise MappingError("quantity value is not finite") from exc
    if number.adjusted() >= MAX_INTEGER_DIGITS:
        raise MappingError("quantity value out of range")
    return number


def _set_value(observation: Observation, resource: Resource) -> None:
    value_type = Observation.ValueType
    if "valueQuantity" in resource:
        quantity = resource["valueQuantity"]
        observation.value_type = value_type.QUANTITY
        observation.value_number = _decimal(quantity.get("value"))
        observation.value_unit = quantity.get("unit") or quantity.get("code") or ""
    elif "valueCodeableConcept" in resource:
        observation.value_type = value_type.CONCEPT
        observation.value_text = _concept_text(resource["valueCodeableConcept"])
    elif scalar := _scalar_value(resource):
        observation.value_type = value_type.STRING
        observation.value_text = scalar
    elif components := resource.get("component"):
        observation.value_type = value_type.COMPONENTS
        observation.components = [_component(component) for component in components]
    else:
        observation.value_type = value_type.NONE


def _scalar_value(resource: Resource) -> str:
    for key in ("valueString", "valueInteger", "valueDateTime"):
        if key in resource:
            return str(resource[key])
    if "valueBoolean" in resource:
        return "true" if resource["valueBoolean"] else "false"
    return ""


def _component(component: Resource) -> dict[str, str | None]:
    """Same rules as the parent value, flattened to strings (JSON-safe, exact)."""
    coding = _preferred_coding(component.get("code") or {})
    value: str | None = None
    unit = ""
    if "valueQuantity" in component:
        number = _decimal(component["valueQuantity"].get("value"))
        value = None if number is None else format_decimal(number)
        unit = (
            component["valueQuantity"].get("unit") or component["valueQuantity"].get("code") or ""
        )
    elif "valueCodeableConcept" in component:
        value = _concept_text(component["valueCodeableConcept"])
    elif scalar := _scalar_value(component):
        value = scalar
    return {
        "code": coding.get("code") or "",
        "display": coding.get("display") or (component.get("code") or {}).get("text") or "",
        "value": value,
        "unit": unit,
    }
