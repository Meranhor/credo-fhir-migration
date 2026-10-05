from django.db import models
from django.utils import timezone


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
