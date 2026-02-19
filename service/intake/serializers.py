from rest_framework import serializers

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


class LawFirmSerializer(serializers.ModelSerializer):
    class Meta:
        model = LawFirm
        fields = "__all__"


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = "__all__"


class CaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Case
        fields = "__all__"


class OtherPartySerializer(serializers.ModelSerializer):
    class Meta:
        model = OtherParty
        fields = "__all__"


class InsuranceProviderSerializer(serializers.ModelSerializer):
    class Meta:
        model = InsuranceProvider
        fields = "__all__"

    def validate(self, data):
        insured_client = data.get("insured_client") or getattr(self.instance, "insured_client", None)
        insured_other_party = data.get("insured_other_party") or getattr(self.instance, "insured_other_party", None)
        if bool(insured_client) == bool(insured_other_party):
            raise serializers.ValidationError(
                "Exactly one of insured_client or insured_other_party must be set."
            )
        return data


class MedicalFacilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicalFacility
        fields = "__all__"


class MedicalProviderSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicalProvider
        fields = "__all__"


class TreatmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Treatment
        fields = "__all__"


class DamageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Damage
        fields = "__all__"


class CitationReferenceSerializer(serializers.ModelSerializer):
    referenced_object_repr = serializers.SerializerMethodField()

    class Meta:
        model = CitationReference
        fields = "__all__"

    def get_referenced_object_repr(self, obj):
        ref = obj.referenced_object
        if ref is None:
            return None
        return str(ref)


class ClientCommunicationCitationSerializer(serializers.ModelSerializer):
    references = CitationReferenceSerializer(many=True, read_only=True)

    class Meta:
        model = ClientCommunicationCitation
        fields = "__all__"


class ClientCommunicationCitationWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientCommunicationCitation
        fields = "__all__"


class ClientCommunicationSerializer(serializers.ModelSerializer):
    citations = ClientCommunicationCitationSerializer(many=True, read_only=True)

    class Meta:
        model = ClientCommunication
        fields = "__all__"


class ClientCommunicationWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientCommunication
        fields = "__all__"
