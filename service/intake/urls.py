from django.urls import path

from rest_framework.routers import DefaultRouter

from .views import (
    CaseViewSet,
    CitationReferenceViewSet,
    ClientCommunicationCitationViewSet,
    ClientCommunicationViewSet,
    ClientViewSet,
    DamageViewSet,
    InsuranceProviderViewSet,
    LawFirmViewSet,
    MedicalFacilityViewSet,
    MedicalProviderViewSet,
    OtherPartyViewSet,
    TreatmentViewSet,
)

router = DefaultRouter()
router.register(r"law-firms", LawFirmViewSet, basename="lawfirm")
router.register(r"clients", ClientViewSet, basename="client")
router.register(r"cases", CaseViewSet, basename="case")
router.register(r"other-parties", OtherPartyViewSet, basename="otherparty")
router.register(r"insurance-providers", InsuranceProviderViewSet, basename="insuranceprovider")
router.register(r"medical-facilities", MedicalFacilityViewSet, basename="medicalfacility")
router.register(r"medical-providers", MedicalProviderViewSet, basename="medicalprovider")
router.register(r"treatments", TreatmentViewSet, basename="treatment")
router.register(r"damages", DamageViewSet, basename="damage")
router.register(r"communications", ClientCommunicationViewSet, basename="clientcommunication")
router.register(r"citations", ClientCommunicationCitationViewSet, basename="clientcommunicationcitation")
router.register(r"citation-references", CitationReferenceViewSet, basename="citationreference")

from .views_html import case_summary  # noqa: E402 — after router setup

urlpatterns = router.urls + [
    path("cases/<str:case_id>/summary/", case_summary, name="case-summary"),
]
