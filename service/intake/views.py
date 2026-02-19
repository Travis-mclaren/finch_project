from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from .models import (
    Case,
    CitationReference,
    Client,
    ClientCommunication,
    ClientCommunicationCitation,
    Damage,
    InsuranceProvider,
    LawFirm,
    MedicalFacility,
    MedicalProvider,
    OtherParty,
    Treatment,
)
from .serializers import (
    CaseSerializer,
    CitationReferenceSerializer,
    ClientCommunicationCitationSerializer,
    ClientCommunicationCitationWriteSerializer,
    ClientCommunicationSerializer,
    ClientCommunicationWriteSerializer,
    ClientSerializer,
    DamageSerializer,
    InsuranceProviderSerializer,
    LawFirmSerializer,
    MedicalFacilitySerializer,
    MedicalProviderSerializer,
    OtherPartySerializer,
    TreatmentSerializer,
)
from .services.transcript_parser import TranscriptParserService


def _public(d: dict) -> dict:
    """Strip internal ``_*`` citation-metadata keys from an extraction result dict."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


class LawFirmViewSet(ModelViewSet):
    queryset = LawFirm.objects.all()
    serializer_class = LawFirmSerializer


class ClientViewSet(ModelViewSet):
    queryset = Client.objects.select_related("law_firm").all()
    serializer_class = ClientSerializer


class CaseViewSet(ModelViewSet):
    queryset = Case.objects.select_related("client").all()
    serializer_class = CaseSerializer

    @action(detail=True, methods=["get"], url_path="communications")
    def communications(self, request, pk=None):
        """List all ClientCommunications associated with this case."""
        case = self.get_object()
        qs = ClientCommunication.objects.filter(case=case).select_related("client")
        serializer = ClientCommunicationSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="analyze")
    def analyze(self, request, pk=None):
        """Run LLM analysis on this case and return a structured evaluation report."""
        from .services.case_analyzer import CaseAnalysisError, analyze_case

        try:
            report = analyze_case(pk)
            return Response(report)
        except Case.DoesNotExist:
            return Response(
                {"error": "Case not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except CaseAnalysisError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class OtherPartyViewSet(ModelViewSet):
    queryset = OtherParty.objects.select_related("case").all()
    serializer_class = OtherPartySerializer


class InsuranceProviderViewSet(ModelViewSet):
    queryset = InsuranceProvider.objects.all()
    serializer_class = InsuranceProviderSerializer


class MedicalFacilityViewSet(ModelViewSet):
    queryset = MedicalFacility.objects.all()
    serializer_class = MedicalFacilitySerializer


class MedicalProviderViewSet(ModelViewSet):
    queryset = MedicalProvider.objects.select_related("facility").all()
    serializer_class = MedicalProviderSerializer


class TreatmentViewSet(ModelViewSet):
    queryset = Treatment.objects.select_related("case", "provider").all()
    serializer_class = TreatmentSerializer


class DamageViewSet(ModelViewSet):
    queryset = Damage.objects.select_related("case").all()
    serializer_class = DamageSerializer


class ClientCommunicationViewSet(ModelViewSet):
    queryset = ClientCommunication.objects.select_related("client", "case").all()

    def get_serializer_class(self):
        if self.request.method in ("POST", "PUT", "PATCH"):
            return ClientCommunicationWriteSerializer
        return ClientCommunicationSerializer

    @action(detail=False, methods=["post"], url_path="ingest")
    def ingest(self, request):
        """Bootstrap ingest: create LawFirm → Client → Case → Communication from a raw transcript."""
        transcript = request.data.get("transcript")
        if not isinstance(transcript, list):
            return Response(
                {"status": "error", "detail": "'transcript' must be a list of turn objects."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        law_firm_id = request.data.get("law_firm_id") or None
        service = TranscriptParserService()
        try:
            communication, result, matched = service.ingest(transcript, law_firm_id=law_firm_id)
        except LawFirm.DoesNotExist:
            return Response(
                {"status": "error", "detail": f"LawFirm '{law_firm_id}' not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            return Response(
                {"status": "error", "detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "status": "ok",
                "matched": matched,
                "law_firm_id": str(communication.client.law_firm_id),
                "client_id": str(communication.client_id),
                "case_id": str(communication.case_id),
                "communication_id": str(communication.pk),
                "result": {
                    "incident_date": result.incident_date,
                    "incident_type": result.incident_type,
                    "incident_location": result.incident_location,
                    "injuries": result.injuries,
                    "medical_providers": [_public(p) for p in result.medical_providers],
                    "insurance_carriers": result.insurance_carriers,
                    "other_parties": [_public(p) for p in result.other_parties],
                    "damages": [_public(d) for d in result.damages],
                    "raw_flags": result.raw_flags,
                },
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="parse")
    def parse(self, request, pk=None):
        """Trigger TranscriptParserService on this communication."""
        communication = self.get_object()
        service = TranscriptParserService()
        try:
            result = service.parse(communication)
            if communication.case_id:
                service.persist(communication.case, result, communication=communication)
            return Response(
                {
                    "status": "ok",
                    "result": {
                        "incident_date": result.incident_date,
                        "incident_type": result.incident_type,
                        "incident_location": result.incident_location,
                        "injuries": result.injuries,
                        "medical_providers": [_public(p) for p in result.medical_providers],
                        "insurance_carriers": result.insurance_carriers,
                        "other_parties": [_public(p) for p in result.other_parties],
                        "damages": [_public(d) for d in result.damages],
                        "confidence_scores": result.confidence_scores,
                        "raw_flags": result.raw_flags,
                    },
                }
            )
        except Exception as exc:
            return Response({"status": "error", "detail": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ClientCommunicationCitationViewSet(ModelViewSet):
    queryset = ClientCommunicationCitation.objects.select_related("communication").all()

    def get_serializer_class(self):
        if self.request.method in ("POST", "PUT", "PATCH"):
            return ClientCommunicationCitationWriteSerializer
        return ClientCommunicationCitationSerializer


class CitationReferenceViewSet(ModelViewSet):
    queryset = CitationReference.objects.select_related("citation", "content_type").all()
    serializer_class = CitationReferenceSerializer
