from django.urls import path

from migration.api.views import PatientDetail, PatientList

urlpatterns = [
    path("patients/", PatientList.as_view(), name="patient-list"),
    path("patients/<int:pk>/", PatientDetail.as_view(), name="patient-detail"),
]
