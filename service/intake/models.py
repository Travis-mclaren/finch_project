import uuid

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models


class LawFirm(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Law Firm"
        verbose_name_plural = "Law Firms"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
        ]

    def __str__(self):
        return self.name


class Client(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    law_firm = models.ForeignKey(LawFirm, on_delete=models.CASCADE, related_name="clients", db_index=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField(null=True, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    ssn_last_four = models.CharField(max_length=4, blank=True, help_text="Last 4 digits of SSN (PII)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Client"
        verbose_name_plural = "Clients"
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["law_firm"]),
            models.Index(fields=["last_name", "first_name"]),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Case(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        SETTLED = "settled", "Settled"
        DROPPED = "dropped", "Dropped"
        TRIAL = "trial", "Trial"
        CLOSED = "closed", "Closed"

    class IncidentType(models.TextChoices):
        AUTO = "auto", "Auto Accident"
        SLIP_FALL = "slip_fall", "Slip & Fall"
        MEDICAL_MALPRACTICE = "medical_malpractice", "Medical Malpractice"
        PRODUCT_LIABILITY = "product_liability", "Product Liability"
        WORKPLACE = "workplace", "Workplace Injury"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="case_set", db_index=True)
    case_number = models.CharField(max_length=100, unique=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    incident_type = models.CharField(max_length=30, choices=IncidentType.choices, default=IncidentType.OTHER)
    incident_date = models.DateField(null=True, blank=True)
    incident_location = models.TextField(blank=True)
    description = models.TextField(blank=True)
    outcome_status = models.CharField(max_length=50, blank=True, help_text="Final outcome label from transcript data")
    outcome_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text="Settlement or award amount")
    statute_of_limitations_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Case"
        verbose_name_plural = "Cases"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["incident_date"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["client"]),
        ]

    def __str__(self):
        return f"Case {self.case_number or self.id} — {self.client}"


class OtherParty(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="other_parties", db_index=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    company_name = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=100, blank=True, help_text="e.g., at-fault driver, property owner")
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Other Party"
        verbose_name_plural = "Other Parties"
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["case"]),
        ]

    def __str__(self):
        if self.company_name:
            return self.company_name
        return f"{self.first_name} {self.last_name}".strip() or str(self.id)


class InsuranceProvider(models.Model):
    class CoverageType(models.TextChoices):
        LIABILITY = "liability", "Liability"
        UNINSURED_MOTORIST = "uninsured_motorist", "Uninsured Motorist"
        MEDICAL_PAYMENTS = "medical_payments", "Medical Payments"
        HEALTH = "health", "Health"
        WORKERS_COMP = "workers_comp", "Workers Compensation"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    insured_client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="insurance_providers",
        null=True,
        blank=True,
        db_index=True,
    )
    insured_other_party = models.ForeignKey(
        OtherParty,
        on_delete=models.CASCADE,
        related_name="insurance_providers",
        null=True,
        blank=True,
        db_index=True,
    )
    company_name = models.CharField(max_length=255)
    policy_number = models.CharField(max_length=100, blank=True)
    claim_number = models.CharField(max_length=100, blank=True)
    coverage_type = models.CharField(max_length=30, choices=CoverageType.choices, default=CoverageType.LIABILITY)
    policy_limit = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    adjuster_name = models.CharField(max_length=255, blank=True)
    adjuster_phone = models.CharField(max_length=50, blank=True)
    adjuster_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Insurance Provider"
        verbose_name_plural = "Insurance Providers"
        ordering = ["company_name"]
        indexes = [
            models.Index(fields=["insured_client"]),
            models.Index(fields=["insured_other_party"]),
        ]

    def clean(self):
        if bool(self.insured_client_id) == bool(self.insured_other_party_id):
            raise ValidationError(
                "Exactly one of insured_client or insured_other_party must be set."
            )

    def __str__(self):
        return f"{self.company_name} ({self.coverage_type})"


class MedicalFacility(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    facility_type = models.CharField(
        max_length=100, blank=True, help_text="e.g., hospital, urgent care, chiropractic"
    )
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    fax = models.CharField(max_length=50, blank=True)
    npi = models.CharField(max_length=20, blank=True, help_text="National Provider Identifier")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Medical Facility"
        verbose_name_plural = "Medical Facilities"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
        ]

    def __str__(self):
        return self.name


class MedicalProvider(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        MedicalFacility, on_delete=models.SET_NULL, related_name="providers", null=True, blank=True, db_index=True
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    specialty = models.CharField(max_length=100, blank=True)
    npi = models.CharField(max_length=20, blank=True, help_text="National Provider Identifier")
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Medical Provider"
        verbose_name_plural = "Medical Providers"
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["facility"]),
            models.Index(fields=["last_name", "first_name"]),
        ]

    def __str__(self):
        return f"Dr. {self.first_name} {self.last_name}"


class Treatment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="treatments", db_index=True)
    provider = models.ForeignKey(
        MedicalProvider, on_delete=models.SET_NULL, related_name="treatments", null=True, blank=True, db_index=True
    )
    treatment_type = models.CharField(max_length=255, blank=True, help_text="e.g., ER visit, MRI, physical therapy")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    diagnosis = models.TextField(blank=True)
    icd_codes = models.JSONField(default=list, blank=True, help_text="List of ICD-10 diagnosis codes")
    billed_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Treatment"
        verbose_name_plural = "Treatments"
        ordering = ["-start_date"]
        indexes = [
            models.Index(fields=["case"]),
            models.Index(fields=["provider"]),
            models.Index(fields=["start_date"]),
        ]

    def __str__(self):
        return f"{self.treatment_type or 'Treatment'} — {self.case}"


class Damage(models.Model):
    class DamageCategory(models.TextChoices):
        MEDICAL = "medical", "Medical Expenses"
        LOST_WAGES = "lost_wages", "Lost Wages"
        PAIN_SUFFERING = "pain_suffering", "Pain & Suffering"
        PROPERTY = "property", "Property Damage"
        FUTURE_MEDICAL = "future_medical", "Future Medical Expenses"
        FUTURE_LOST_WAGES = "future_lost_wages", "Future Lost Wages"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="damages", db_index=True)
    category = models.CharField(max_length=30, choices=DamageCategory.choices, default=DamageCategory.OTHER)
    description = models.TextField(blank=True)
    estimated_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    documented = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Damage"
        verbose_name_plural = "Damages"
        ordering = ["category"]
        indexes = [
            models.Index(fields=["case"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self):
        return f"{self.get_category_display()} — {self.case}"


class ClientCommunication(models.Model):
    class ChannelType(models.TextChoices):
        PHONE = "phone", "Phone Call"
        IN_PERSON = "in_person", "In Person"
        EMAIL = "email", "Email"
        TEXT = "text", "Text Message"
        PORTAL = "portal", "Client Portal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="communications", db_index=True)
    case = models.ForeignKey(
        Case, on_delete=models.SET_NULL, related_name="communications", null=True, blank=True, db_index=True
    )
    channel = models.CharField(max_length=20, choices=ChannelType.choices, default=ChannelType.PHONE)
    occurred_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    summary = models.TextField(blank=True)
    raw_transcript = models.JSONField(
        default=list,
        blank=True,
        help_text="Array of transcript turn objects: [{speaker, text, timestamp}]",
    )
    parse_status = models.CharField(
        max_length=20,
        choices=[("pending", "Pending"), ("processing", "Processing"), ("done", "Done"), ("failed", "Failed")],
        default="pending",
    )
    external_id = models.CharField(max_length=255, blank=True, help_text="ID from source system (e.g. sample_transcripts.json)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Client Communication"
        verbose_name_plural = "Client Communications"
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["occurred_at"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["client"]),
            models.Index(fields=["case"]),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} with {self.client} at {self.occurred_at or self.created_at}"


class ClientCommunicationCitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    communication = models.ForeignKey(
        ClientCommunication, on_delete=models.CASCADE, related_name="citations", db_index=True
    )
    citation_key = models.CharField(max_length=100, help_text="Semantic key, e.g. 'incident_date', 'at_fault_party'")
    cited_text = models.TextField(help_text="The verbatim excerpt from the transcript supporting this citation")
    turn_index = models.PositiveIntegerField(null=True, blank=True, help_text="Index into raw_transcript array")
    confidence_score = models.FloatField(default=1.0, help_text="Parser confidence 0.0–1.0")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Client Communication Citation"
        verbose_name_plural = "Client Communication Citations"
        ordering = ["citation_key", "-confidence_score"]
        indexes = [
            models.Index(fields=["communication"]),
            models.Index(fields=["citation_key", "confidence_score"]),
        ]

    def __str__(self):
        return f"{self.citation_key} ({self.confidence_score:.2f}) — {self.communication}"


class CitationReference(models.Model):
    """Polymorphic link from a citation to a Client, OtherParty, MedicalProvider, or InsuranceProvider."""

    ALLOWED_CONTENT_TYPES = ["client", "otherparty", "medicalprovider", "insuranceprovider"]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    citation = models.ForeignKey(
        ClientCommunicationCitation, on_delete=models.CASCADE, related_name="references", db_index=True
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, db_index=True)
    object_id = models.CharField(max_length=36, help_text="UUID of the referenced object")
    referenced_object = GenericForeignKey("content_type", "object_id")
    relationship_label = models.CharField(
        max_length=100, blank=True, help_text="Human label for this reference, e.g. 'treating physician'"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Citation Reference"
        verbose_name_plural = "Citation References"
        ordering = ["citation"]
        indexes = [
            models.Index(fields=["citation"]),
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        return f"Ref({self.content_type}, {self.object_id}) ← {self.citation}"
