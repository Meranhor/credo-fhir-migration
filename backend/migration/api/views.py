from django.db.models import Count, F, Max, Prefetch, QuerySet
from rest_framework import generics

from migration.api.serializers import PatientDetailSerializer, PatientListSerializer
from migration.models import Observation, Patient


class PatientList(generics.ListAPIView[Patient]):
    """All migrated patients with their observation count, in one SQL query."""

    serializer_class = PatientListSerializer

    def get_queryset(self) -> QuerySet[Patient]:
        return (
            Patient.objects.only(
                "id", "fhir_id", "family_name", "given_names", "gender", "birth_date"
            )
            .annotate(
                observation_count=Count("observations"),
                last_observation_at=Max("observations__effective_at"),
            )
            .order_by("family_name", "given_names", "id")
        )


class PatientDetail(generics.RetrieveAPIView[Patient]):
    """One patient and their observations, most recent first, in two SQL queries."""

    serializer_class = PatientDetailSerializer

    def get_queryset(self) -> QuerySet[Patient]:
        observations = Observation.objects.defer("raw").order_by(
            F("effective_at").desc(nulls_last=True), "id"
        )
        return Patient.objects.defer("raw").prefetch_related(
            Prefetch("observations", queryset=observations)
        )
