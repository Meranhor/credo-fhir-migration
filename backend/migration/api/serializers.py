from rest_framework import serializers

from migration.models import Observation, Patient

# `raw` (the full source resource) is never serialised: minimum necessary.


class PatientListSerializer(serializers.ModelSerializer[Patient]):
    name = serializers.CharField(read_only=True)
    observation_count = serializers.IntegerField(read_only=True)
    last_observation_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = Patient
        fields = [
            "id",
            "fhir_id",
            "name",
            "gender",
            "birth_date",
            "observation_count",
            "last_observation_at",
        ]


class ObservationSerializer(serializers.ModelSerializer[Observation]):
    value = serializers.CharField(source="value_display", read_only=True)

    class Meta:
        model = Observation
        fields = [
            "id",
            "fhir_id",
            "status",
            "category",
            "code_system",
            "code",
            "display",
            "value",
            "value_type",
            "value_number",
            "value_unit",
            "value_text",
            "components",
            "effective_at",
            "effective_date_only",
        ]


class PatientDetailSerializer(serializers.ModelSerializer[Patient]):
    name = serializers.CharField(read_only=True)
    observations = ObservationSerializer(many=True, read_only=True)

    class Meta:
        model = Patient
        fields = [
            "id",
            "fhir_id",
            "name",
            "family_name",
            "given_names",
            "gender",
            "birth_date",
            "observations",
        ]
