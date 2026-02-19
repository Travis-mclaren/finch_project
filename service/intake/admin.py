from django.contrib import admin

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


@admin.register(LawFirm)
class LawFirmAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "created_at")
    search_fields = ("name", "email")
    list_filter = ("created_at",)


class CaseInline(admin.TabularInline):
    model = Case
    extra = 0
    fields = ("case_number", "status", "incident_type", "incident_date")
    show_change_link = True


class InsuranceProviderInline(admin.TabularInline):
    model = InsuranceProvider
    extra = 0
    fields = ("company_name", "coverage_type", "policy_number", "claim_number")
    show_change_link = True
    fk_name = "insured_client"


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("__str__", "law_firm", "email", "phone", "created_at")
    search_fields = ("first_name", "last_name", "email")
    list_filter = ("law_firm", "created_at")
    inlines = [CaseInline, InsuranceProviderInline]


class OtherPartyInline(admin.TabularInline):
    model = OtherParty
    extra = 0
    fields = ("first_name", "last_name", "company_name", "role")
    show_change_link = True


class TreatmentInline(admin.TabularInline):
    model = Treatment
    extra = 0
    fields = ("treatment_type", "provider", "start_date", "billed_amount")
    show_change_link = True


class DamageInline(admin.TabularInline):
    model = Damage
    extra = 0
    fields = ("category", "estimated_amount", "documented")
    show_change_link = True


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ("case_number", "client", "status", "incident_type", "incident_date", "outcome_value", "created_at")
    search_fields = ("case_number", "client__first_name", "client__last_name")
    list_filter = ("status", "incident_type", "created_at")
    inlines = [OtherPartyInline, TreatmentInline, DamageInline]


@admin.register(OtherParty)
class OtherPartyAdmin(admin.ModelAdmin):
    list_display = ("__str__", "case", "role", "phone")
    search_fields = ("first_name", "last_name", "company_name")
    list_filter = ("case__status",)


@admin.register(InsuranceProvider)
class InsuranceProviderAdmin(admin.ModelAdmin):
    list_display = ("company_name", "coverage_type", "policy_number", "claim_number", "adjuster_name")
    search_fields = ("company_name", "policy_number", "claim_number", "adjuster_name")
    list_filter = ("coverage_type",)


@admin.register(MedicalFacility)
class MedicalFacilityAdmin(admin.ModelAdmin):
    list_display = ("name", "facility_type", "phone", "npi")
    search_fields = ("name", "npi")
    list_filter = ("facility_type",)


@admin.register(MedicalProvider)
class MedicalProviderAdmin(admin.ModelAdmin):
    list_display = ("__str__", "specialty", "facility", "npi", "phone")
    search_fields = ("first_name", "last_name", "npi", "specialty")
    list_filter = ("specialty", "facility")


@admin.register(Treatment)
class TreatmentAdmin(admin.ModelAdmin):
    list_display = ("treatment_type", "case", "provider", "start_date", "billed_amount", "paid_amount")
    search_fields = ("treatment_type", "diagnosis")
    list_filter = ("start_date",)


@admin.register(Damage)
class DamageAdmin(admin.ModelAdmin):
    list_display = ("category", "case", "estimated_amount", "documented")
    search_fields = ("description",)
    list_filter = ("category", "documented")


class CitationReferenceInline(admin.TabularInline):
    model = CitationReference
    extra = 0
    fields = ("content_type", "object_id", "relationship_label")
    show_change_link = True


class ClientCommunicationCitationInline(admin.TabularInline):
    model = ClientCommunicationCitation
    extra = 0
    fields = ("citation_key", "cited_text", "confidence_score", "turn_index")
    show_change_link = True


@admin.register(ClientCommunication)
class ClientCommunicationAdmin(admin.ModelAdmin):
    list_display = ("__str__", "client", "case", "channel", "occurred_at", "parse_status")
    search_fields = ("client__first_name", "client__last_name", "external_id", "summary")
    list_filter = ("channel", "parse_status", "occurred_at")
    inlines = [ClientCommunicationCitationInline]


@admin.register(ClientCommunicationCitation)
class ClientCommunicationCitationAdmin(admin.ModelAdmin):
    list_display = ("citation_key", "communication", "confidence_score", "turn_index")
    search_fields = ("citation_key", "cited_text")
    list_filter = ("citation_key",)
    inlines = [CitationReferenceInline]


@admin.register(CitationReference)
class CitationReferenceAdmin(admin.ModelAdmin):
    list_display = ("citation", "content_type", "object_id", "relationship_label")
    search_fields = ("object_id", "relationship_label")
    list_filter = ("content_type",)
