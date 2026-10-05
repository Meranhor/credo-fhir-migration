from decimal import Decimal

from django.db import models
from django.utils import timezone


def format_decimal(number: Decimal) -> str:
    """120.000000 -> "120", 98.390000 -> "98.39" (no exponent, no trailing zeros)."""
    return format(number.normalize(), "f")


class Patient(models.Model):
    class Gender(models.TextChoices):
        MALE = "male"
        FEMALE = "female"
        OTHER = "other"
        UNKNOWN = "unknown"

    fhir_id = models.CharField(max_length=64, unique=True)
    family_name = models.CharField(max_length=255, blank=True)
    given_names = models.CharField(max_length=255, blank=True)
    gender = models.CharField(max_length=16, choices=Gender.choices, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    raw = models.JSONField()
    migrated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        # Identifier only: a name must never end up in a log line by accident.
        return f"Patient {self.fhir_id}"

    @property
    def name(self) -> str:
        return f"{self.given_names} {self.family_name}".strip()


class Observation(models.Model):
    class ValueType(models.TextChoices):
        QUANTITY = "quantity"
        CONCEPT = "concept"
        STRING = "string"
        COMPONENTS = "components"
        NONE = "none"

    fhir_id = models.CharField(max_length=64, unique=True)
    # Indexed through the composite index below (same leading column).
    patient = models.ForeignKey(
        Patient, on_delete=models.CASCADE, related_name="observations", db_index=False
    )
    status = models.CharField(max_length=32, blank=True)
    category = models.CharField(max_length=64, blank=True)
    code_system = models.CharField(max_length=255, blank=True)
    code = models.CharField(max_length=64, blank=True)
    display = models.TextField(blank=True)
    value_type = models.CharField(max_length=16, choices=ValueType.choices)
    value_number = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    value_unit = models.CharField(max_length=64, blank=True)
    value_text = models.TextField(blank=True)
    components = models.JSONField(null=True, blank=True)
    effective_at = models.DateTimeField(null=True, blank=True)
    # The source gave a day without a time: effective_at is that day at 00:00 UTC.
    effective_date_only = models.BooleanField(default=False)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    raw = models.JSONField()
    migrated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # Patient detail view: observations of one patient, most recent first.
            models.Index(fields=["patient", "-effective_at"], name="obs_patient_effective_idx"),
        ]

    def __str__(self) -> str:
        return f"Observation {self.fhir_id}"

    @property
    def value_display(self) -> str:
        """Human-readable value, e.g. "98.39 mg/dL", "Never smoker", "120/80 mm[Hg]"."""
        if self.value_type == self.ValueType.QUANTITY:
            number = "" if self.value_number is None else format_decimal(self.value_number)
            return f"{number} {self.value_unit}".strip()
        if self.value_type == self.ValueType.COMPONENTS:
            return _components_display(self.components or [])
        return self.value_text


def _components_display(components: list[dict[str, str | None]]) -> str:
    values = [component.get("value") or "?" for component in components]
    units = {component.get("unit") or "" for component in components}
    if len(units) == 1:  # blood pressure style: "120/80 mm[Hg]"
        return f"{'/'.join(values)} {units.pop()}".strip()
    return "; ".join(
        f"{c.get('display') or c.get('code')}: {value} {c.get('unit') or ''}".strip()
        for c, value in zip(components, values, strict=True)
    )


class MigrationRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    batches = models.PositiveIntegerField(default=0)
    patients_written = models.PositiveIntegerField(default=0)
    observations_written = models.PositiveIntegerField(default=0)
    skipped_invalid = models.PositiveIntegerField(default=0)
    skipped_orphans = models.PositiveIntegerField(default=0)
    count_mismatches = models.PositiveIntegerField(default=0)
    http_requests = models.PositiveIntegerField(default=0)
    http_retries = models.PositiveIntegerField(default=0)
    bytes_received = models.PositiveBigIntegerField(default=0)
    error = models.TextField(blank=True)  # never PHI: exception type or client message only

    def __str__(self) -> str:
        return f"MigrationRun {self.pk} ({self.status})"

    def finish(self, status: Status, error: str = "") -> None:
        self.status = status
        self.error = error
        self.finished_at = timezone.now()
        self.save()
