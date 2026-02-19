"""
TranscriptParserService — LLM-backed intake extraction.

All private extraction helpers make a single OpenAI API call (via _call_llm)
and cache the structured findings for the lifetime of one parse() invocation.
The public interface (parse / persist) is unchanged.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

import openai
from django.conf import settings

if TYPE_CHECKING:
    from intake.models import Case, ClientCommunication

logger = logging.getLogger(__name__)


@dataclass
class IntakeExtractionResult:
    incident_date: date | None = None
    incident_type: str | None = None
    incident_location: str | None = None
    injuries: list[str] = field(default_factory=list)
    medical_providers: list[dict] = field(default_factory=list)
    insurance_carriers: list[dict] = field(default_factory=list)
    other_parties: list[dict] = field(default_factory=list)
    damages: list[dict] = field(default_factory=list)
    confidence_scores: dict[str, float] = field(default_factory=dict)
    raw_flags: list[str] = field(default_factory=list)


class TranscriptParserService:
    """
    Extracts structured intake data from a ClientCommunication transcript.

    Usage::

        service = TranscriptParserService()
        result = service.parse(communication)
        service.persist(case, result)
    """

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def parse(self, communication: ClientCommunication) -> IntakeExtractionResult:
        """
        Parse a communication's raw_transcript and return an extraction result.

        Steps:
          1. Normalise turns from raw_transcript JSON.
          2. Run each extraction sub-method.
          3. Combine into IntakeExtractionResult.
          4. Flag risks.
          5. Update communication.parse_status.
        """
        turns: list[dict] = communication.raw_transcript or []

        incident_info = self._extract_incident_info(turns)
        parties = self._extract_parties(turns)
        medical = self._extract_medical(turns)
        insurance = self._extract_insurance(turns)
        damages = self._extract_damages(turns)

        result = IntakeExtractionResult(
            incident_date=incident_info.get("incident_date"),
            incident_type=incident_info.get("incident_type"),
            incident_location=incident_info.get("incident_location"),
            injuries=incident_info.get("injuries", []),
            medical_providers=medical,
            insurance_carriers=insurance,
            other_parties=parties,
            damages=damages,
            confidence_scores=incident_info.get("confidence_scores", {}),
        )
        result.raw_flags = self._flag_risks(result)

        # Update parse status on the communication record
        communication.parse_status = "done"
        communication.save(update_fields=["parse_status", "updated_at"])

        return result

    def persist(self, case: Case, result: IntakeExtractionResult, communication=None) -> None:
        """
        Persist an IntakeExtractionResult to the database.

        Creates / updates:
          - OtherParty records for each entry in result.other_parties
          - MedicalProvider + Treatment records
          - Damage records
          - Updates Case.incident_date / incident_type if not already set

        When ``communication`` is provided, a ClientCommunicationCitation is written
        for every object created, with a CitationReference linking to it when the
        model is in CitationReference.ALLOWED_CONTENT_TYPES.
        """
        from django.contrib.contenttypes.models import ContentType
        from intake.models import (
            ClientCommunicationCitation,
            CitationReference,
            Damage,
            MedicalFacility,
            MedicalProvider,
            OtherParty,
            Treatment,
        )

        _CONF = {"high": 1.0, "medium": 0.7, "low": 0.4}

        def _cite(citation_key: str, data: dict, obj=None, label: str = "") -> None:
            """Create a citation (and optional reference) from a data dict with _* metadata."""
            if not communication:
                return
            cit = ClientCommunicationCitation.objects.create(
                communication=communication,
                citation_key=citation_key,
                cited_text=data.get("_cited_text") or "",
                turn_index=data.get("_turn_index"),
                confidence_score=_CONF.get(str(data.get("_confidence", "high")).lower(), 1.0),
            )
            if obj is not None:
                ct = ContentType.objects.get_for_model(obj)
                if ct.model in CitationReference.ALLOWED_CONTENT_TYPES:
                    CitationReference.objects.create(
                        citation=cit,
                        content_type=ct,
                        object_id=str(obj.pk),
                        relationship_label=label,
                    )

        # Update case fields if blank
        changed_fields = []
        if result.incident_date and not case.incident_date:
            case.incident_date = result.incident_date
            changed_fields.append("incident_date")
        if result.incident_type and not case.incident_type:
            case.incident_type = result.incident_type
            changed_fields.append("incident_type")
        if changed_fields:
            case.save(update_fields=changed_fields + ["updated_at"])

        # Other parties
        for party_data in result.other_parties:
            party, created = OtherParty.objects.get_or_create(
                case=case,
                first_name=party_data.get("first_name", ""),
                last_name=party_data.get("last_name", ""),
                defaults={
                    "company_name": party_data.get("company_name", ""),
                    "role": party_data.get("role", ""),
                },
            )
            if created:
                _cite("other_party", party_data, obj=party, label="at-fault party")

        # Medical providers + treatments
        for mp_data in result.medical_providers:
            facility_name = mp_data.get("facility_name", "")
            facility = None
            if facility_name:
                facility, _ = MedicalFacility.objects.get_or_create(name=facility_name)

            provider, created = MedicalProvider.objects.get_or_create(
                first_name=mp_data.get("first_name", ""),
                last_name=mp_data.get("last_name", ""),
                defaults={"facility": facility, "specialty": mp_data.get("specialty", "")},
            )
            if created:
                _cite("medical_provider", mp_data, obj=provider, label="treating provider")

            Treatment.objects.get_or_create(
                case=case,
                provider=provider,
                defaults={
                    "treatment_type": mp_data.get("treatment_type", ""),
                    "diagnosis": mp_data.get("diagnosis", ""),
                },
            )

        # Damages
        for dmg_data in result.damages:
            damage, created = Damage.objects.get_or_create(
                case=case,
                category=dmg_data.get("category", "other"),
                defaults={
                    "description": dmg_data.get("description", ""),
                    "estimated_amount": dmg_data.get("estimated_amount"),
                },
            )
            if created:
                _cite("financial_expense", dmg_data)

    def ingest(
        self,
        turns: list[dict],
        law_firm_id: str | None = None,
    ) -> tuple[ClientCommunication, IntakeExtractionResult, bool]:
        """
        Full bootstrap ingest: resolve or create LawFirm → Client → Case →
        ClientCommunication, run extraction, persist downstream records, and return
        the communication, extraction result, and a ``matched`` flag.

        ``matched=True`` means an existing Client + Case were found and reused;
        ``matched=False`` means new records were created.

        Raises:
          LawFirm.DoesNotExist  — if law_firm_id is provided but not found (view maps to 400)
          RuntimeError          — on LLM failure (view maps to 500)
        """
        from intake.models import Case, Client, ClientCommunication, LawFirm

        # 1. Single LLM call — all _extract_* methods share this cache
        findings = self._call_llm(turns)

        # 2. Index metadata findings by field
        meta: dict[str, dict] = {
            f["field"]: f
            for f in findings
            if f.get("finding_type") == "metadata"
        }

        # 3. LawFirm
        if law_firm_id:
            law_firm = LawFirm.objects.get(pk=law_firm_id)
        else:
            firm_name = (meta.get("law_firm_name") or {}).get("value") or "Unknown Law Firm"
            law_firm, _ = LawFirm.objects.get_or_create(name=firm_name)

        # 4. Extract caller identity + incident info (cache hits after step 1)
        caller_name = (meta.get("caller_name") or {}).get("value") or ""
        name_parts = caller_name.strip().split(" ", 1)
        first_name = name_parts[0] if len(name_parts) == 2 else ""
        last_name = name_parts[1] if len(name_parts) == 2 else name_parts[0]
        incident_info = self._extract_incident_info(turns)

        # 5. Try to match an existing Client + Case before creating anything new
        matched = False
        existing = self._find_existing_case(
            law_firm=law_firm,
            first_name=first_name,
            last_name=last_name,
            incident_type=incident_info.get("incident_type"),
            incident_date=incident_info.get("incident_date"),
            incident_location=incident_info.get("incident_location"),
        )

        if existing:
            client, case = existing
            matched = True
        else:
            # Create new Client (get_or_create handles duplicate calls)
            client, _ = Client.objects.get_or_create(
                law_firm=law_firm,
                first_name=first_name,
                last_name=last_name,
            )
            # Always create a new Case when no match is found
            case = Case.objects.create(
                client=client,
                case_number=f"INTAKE-{uuid4().hex[:8].upper()}",
                incident_type=incident_info.get("incident_type") or "",
                incident_date=incident_info.get("incident_date"),
                incident_location=incident_info.get("incident_location") or "",
            )

        # 6. ClientCommunication
        communication = ClientCommunication.objects.create(
            client=client,
            case=case,
            channel="phone",
            raw_transcript=turns,
            parse_status="processing",
        )

        # 6b. Metadata citations — only for newly created records
        if not matched:
            self._write_metadata_citations(communication, meta, client)

        # 7. Build IntakeExtractionResult (all _extract_* are cache hits)
        result = IntakeExtractionResult(
            incident_date=incident_info.get("incident_date"),
            incident_type=incident_info.get("incident_type"),
            incident_location=incident_info.get("incident_location"),
            injuries=incident_info.get("injuries", []),
            medical_providers=self._extract_medical(turns),
            insurance_carriers=self._extract_insurance(turns),
            other_parties=self._extract_parties(turns),
            damages=self._extract_damages(turns),
            confidence_scores=incident_info.get("confidence_scores", {}),
        )

        # 8. Flag risks
        result.raw_flags = self._flag_risks(result)

        # 9. Persist downstream records (with communication so citations are written)
        self.persist(case, result, communication=communication)

        # 10. Mark done
        communication.parse_status = "done"
        communication.save(update_fields=["parse_status", "updated_at"])

        # 11. Return
        return communication, result, matched

    def _write_metadata_citations(self, communication, meta: dict, client) -> None:
        """
        Write ClientCommunicationCitation records for metadata findings that drove
        the creation of Client and Case records during ingest().

        ``meta`` is the {field: finding_dict} index built from the LLM findings.
        A CitationReference to the Client object is attached to the caller_name citation.
        Incident-field citations (accident_date, case_type, incident_location) point
        to the Case implicitly through the communication; no CitationReference is
        created for Case since it is not in CitationReference.ALLOWED_CONTENT_TYPES.
        """
        from django.contrib.contenttypes.models import ContentType
        from intake.models import ClientCommunicationCitation, CitationReference

        _CONF = {"high": 1.0, "medium": 0.7, "low": 0.4}

        def _cite_meta(citation_key: str, finding: dict | None, obj=None, label: str = "") -> None:
            if not finding:
                return
            cit = ClientCommunicationCitation.objects.create(
                communication=communication,
                citation_key=citation_key,
                cited_text=finding.get("quote") or str(finding.get("value", "")),
                turn_index=finding.get("transcript_index"),
                confidence_score=_CONF.get(str(finding.get("confidence", "high")).lower(), 1.0),
            )
            if obj is not None:
                ct = ContentType.objects.get_for_model(obj)
                if ct.model in CitationReference.ALLOWED_CONTENT_TYPES:
                    CitationReference.objects.create(
                        citation=cit,
                        content_type=ct,
                        object_id=str(obj.pk),
                        relationship_label=label,
                    )

        _cite_meta("caller_name", meta.get("caller_name"), obj=client, label="identified caller")
        _cite_meta("accident_date", meta.get("accident_date"))
        _cite_meta("case_type", meta.get("case_type"))
        _cite_meta("incident_location", meta.get("incident_location"))

    def _find_existing_case(
        self,
        law_firm,
        first_name: str,
        last_name: str,
        incident_type: str | None,
        incident_date: date | None,
        incident_location: str | None,
    ):
        """
        Look for an existing Client + Case that matches the caller and incident.

        Matching strategy (in priority order):
          1. Client: case-insensitive first + last name within the same law firm.
          2. Case by incident_date (exact) — strongest signal; same person, same date
             almost certainly the same incident.
          3. Case by incident_type + incident_location substring — fallback when date
             is unavailable or ambiguous.

        Returns a ``(Client, Case)`` tuple on match, or ``None`` if no match found.
        """
        from intake.models import Client

        # --- Client match ---
        client_qs = Client.objects.filter(
            law_firm=law_firm,
            first_name__iexact=first_name,
            last_name__iexact=last_name,
        )
        if not client_qs.exists():
            return None

        # Prefer the most recently created client if there are duplicates
        client = client_qs.order_by("-created_at").first()

        case_qs = client.case_set.all()
        if not case_qs.exists():
            return None

        # --- Case match: incident_date (primary) ---
        if incident_date:
            date_match = case_qs.filter(incident_date=incident_date).first()
            if date_match:
                logger.info(
                    "ingest: matched existing case %s via client+date (%s %s, %s)",
                    date_match.pk, first_name, last_name, incident_date,
                )
                return client, date_match

        # --- Case match: incident_type + location substring (fallback) ---
        if incident_type and incident_location:
            # Use the first 40 chars of the location as a substring anchor to
            # avoid over-matching on very short or generic location strings.
            location_anchor = incident_location[:40].strip()
            type_loc_match = case_qs.filter(
                incident_type=incident_type,
                incident_location__icontains=location_anchor,
            ).first()
            if type_loc_match:
                logger.info(
                    "ingest: matched existing case %s via client+type+location (%s %s, %s, %s)",
                    type_loc_match.pk, first_name, last_name, incident_type, location_anchor,
                )
                return client, type_loc_match

        return None

    # ------------------------------------------------------------------
    # LLM integration — single API call, shared across all extractors
    # ------------------------------------------------------------------

    _SYSTEM_PROMPT = """\
You are a legal intake AI assistant specializing in personal injury law. You analyze \
transcripts from intake calls at personal injury law firms. Approximately 90% of calls \
involve assessing claim viability for potential clients who may have been injured due to \
someone else's negligence.

Your job is to extract structured information from the transcript and return it as a \
JSON object. Be precise and conservative — do NOT guess or invent information. If you are \
not confident about a value, return null for that field.

Extract the following categories:

METADATA (one finding per field):
  - caller_name       : Full name of the person calling
  - law_firm_name     : Name of the law firm the Intake Specialist represents
  - case_type         : One of: auto_accident, slip_fall, medical_malpractice,
                        workers_comp, wrongful_death, product_liability, other
  - accident_date     : Date of the accident/incident in ISO format (YYYY-MM-DD), or null.
                        Dates may be spoken in any format — "March 3rd", "3/3", "the third
                        of March", "last Tuesday", or relative references like "two weeks
                        ago". Convert all formats to ISO (YYYY-MM-DD). For relative dates,
                        use the transcript context clues to anchor the date if possible;
                        if the year is ambiguous, prefer the most recent plausible year.
                        Do NOT return null simply because the date was not in ISO format —
                        only return null if no date reference exists at all.
  - incident_location : Where the incident occurred (city, address, or description)
  - injuries          : Comma-separated list of injuries the caller describes, or null

INDIVIDUAL FINDINGS (one finding per discovered entity, no duplicates):
  - other_party       : Individuals or entities named as at-fault or adverse parties
  - insurance_provider: Insurance companies mentioned (either party's insurer)
  - medical_provider  : Any doctor, hospital, clinic, therapist, chiropractor, urgent care,
                        emergency room, specialist, or other medical/rehabilitation service
                        mentioned — regardless of whether a cost was discussed. Capture the
                        provider even if the caller only says they "went to", "saw",
                        "visited", or "have an appointment with" them.
  - financial_expense : Specific costs, bills, lost wages, property damage estimates, or
                        other financial damages the caller has discussed. A dollar amount
                        is NOT required — capture any expense the caller references even
                        if the amount is unknown or not yet determined (e.g. "my medical
                        bills are piling up", "I missed two weeks of work"). Use a
                        descriptive label for the value if no amount is given.
  - treatment         : Any medical treatment, procedure, therapy session, prescription,
                        or rehabilitation activity the caller has received or is receiving,
                        even if no provider name or cost is associated with it (e.g.
                        "I've been doing physical therapy", "they gave me pain medication",
                        "I'm in a boot"). One finding per distinct treatment type.

For every individual finding include ALL of the following citation fields:
  - transcript_index      : The 0-based index of the transcript turn where this entity is
                            FIRST mentioned. Search carefully — dates, names, and providers
                            are often introduced early and referenced again later. Always
                            cite the FIRST occurrence.
  - transcript_indices    : An array of ALL 0-based turn indices where this entity is
                            mentioned or referenced, including indirect references and
                            pronouns that clearly refer back to this entity.
  - quote                 : The verbatim excerpt (≤ 2 sentences) from the cited turn that
                            most directly establishes this finding. Pull from the turn at
                            transcript_index.
  - confidence            : One of: "high", "medium", "low". Use "high" when explicitly
                            stated, "medium" when strongly implied, "low" when inferred
                            from limited context.
  - related_to            : An object with keys caller, other_party, insurance_provider,
                            medical_provider — set each to the relevant name string if this
                            entity is connected to them. If the connection is POSSIBLE but
                            not confirmed, prefix the value with "possible: " (e.g.
                            "possible: State Farm"). Only set to null if there is truly no
                            plausible connection.

Return ONLY valid JSON in this exact envelope — no markdown, no extra keys:
{
  "findings": [
    {
      "finding_type": "metadata",
      "field": "caller_name",
      "value": "Jane Smith",
      "transcript_index": 2,
      "transcript_indices": [2, 5, 11],
      "quote": "My name is Jane Smith, I was calling about an accident.",
      "confidence": "high"
    },
    {
      "finding_type": "individual",
      "field": "medical_provider",
      "value": "St. Mary's Hospital",
      "transcript_index": 7,
      "transcript_indices": [7, 9, 14],
      "quote": "I went to St. Mary's Hospital the night of the accident.",
      "confidence": "high",
      "related_to": {
        "caller": "Jane Smith",
        "other_party": null,
        "insurance_provider": "possible: State Farm",
        "medical_provider": null
      }
    },
    {
      "finding_type": "individual",
      "field": "financial_expense",
      "value": "Ongoing medical bills (amount unknown)",
      "transcript_index": 12,
      "transcript_indices": [12],
      "quote": "My medical bills are just piling up and I don't know what to do.",
      "confidence": "medium",
      "related_to": {
        "caller": "Jane Smith",
        "other_party": null,
        "insurance_provider": null,
        "medical_provider": "possible: St. Mary's Hospital"
      }
    },
    {
      "finding_type": "individual",
      "field": "treatment",
      "value": "Physical therapy",
      "transcript_index": 15,
      "transcript_indices": [15, 18],
      "quote": "I've been doing physical therapy twice a week since the accident.",
      "confidence": "high",
      "related_to": {
        "caller": "Jane Smith",
        "other_party": null,
        "insurance_provider": null,
        "medical_provider": null
      }
    }
  ]
}
"""

    # Maps LLM case_type values → Case.IncidentType choices
    _INCIDENT_TYPE_MAP: dict[str, str] = {
        "auto_accident": "auto",
        "auto accident": "auto",
        "slip_fall": "slip_fall",
        "slip and fall": "slip_fall",
        "medical_malpractice": "medical_malpractice",
        "medical malpractice": "medical_malpractice",
        "workers_comp": "workplace",
        "workers compensation": "workplace",
        "workplace": "workplace",
        "product_liability": "product_liability",
        "product liability": "product_liability",
        "wrongful_death": "other",
        "wrongful death": "other",
        "other": "other",
    }

    # Keywords that suggest a value is a facility/company rather than an individual
    _FACILITY_KEYWORDS = frozenset(
        ["hospital", "clinic", "center", "centre", "medical", "health", "urgent care",
         "orthopedic", "chiropractic", "chiropractor", "rehab", "rehabilitation",
         "imaging", "radiology", "pharmacy", "er ", "emergency room"]
    )
    _COMPANY_KEYWORDS = frozenset(
        ["inc", "llc", "corp", "co.", "company", "ltd", "group", "trucking",
         "transport", "logistics", "construction", "properties", "management"]
    )

    def _call_llm(self, turns: list[dict]) -> list[dict]:
        """
        Make a single OpenAI API call to extract all findings from the transcript.

        Results are cached on the instance keyed by the id() of the turns list, so
        all _extract_* methods called within one parse() invocation share one call.

        Returns a list of finding dicts as described in the system prompt, with null-
        valued entries already filtered out.

        Raises RuntimeError on API failure or unparseable response (never silently
        returns empty results on error).
        """
        # Cache keyed by identity of the turns list object (same object across one
        # parse() call; a new parse() call creates a new list with a new id).
        cache = getattr(self, "_findings_cache", None)
        if cache is not None and cache.get("turns_id") == id(turns):
            return cache["data"]

        # Short-circuit for empty/very short transcripts
        if not turns:
            self._findings_cache = {"turns_id": id(turns), "data": []}
            return []

        api_key = getattr(settings, "OPENAPI_KEY", None)
        if not api_key:
            raise RuntimeError(
                "OPENAPI_KEY is not set. Add it to your .env file and ensure "
                "load_dotenv() is called in settings.py."
            )

        client = openai.OpenAI(api_key=api_key)

        # Render transcript as indexed lines — only the content reaches the LLM
        transcript_text = "\n".join(
            f"[{i}] {turn.get('speaker', 'Unknown')}: {turn.get('text', '')}"
            for i, turn in enumerate(turns)
        )

        try:
            response = client.chat.completions.create(
                model="gpt-5",
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": self._SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "Extract all findings from this personal injury intake "
                            f"call transcript:\n\n{transcript_text}"
                        ),
                    },
                ],
            )
        except openai.OpenAIError as exc:
            logger.error(
                "OpenAI API call failed in TranscriptParserService._call_llm: %s", exc
            )
            raise RuntimeError(f"LLM extraction failed: {exc}") from exc

        raw_content = response.choices[0].message.content
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            logger.error(
                "TranscriptParserService._call_llm: could not parse LLM response as "
                "JSON. Raw content: %.500s", raw_content
            )
            raise RuntimeError(f"LLM returned non-JSON response: {exc}") from exc

        findings = parsed.get("findings", [])
        if not isinstance(findings, list):
            logger.error(
                "TranscriptParserService._call_llm: 'findings' key missing or not a "
                "list. Parsed: %s", parsed
            )
            raise RuntimeError("LLM response missing 'findings' list.")

        # Filter out null-valued findings — these signal LLM uncertainty
        valid = [f for f in findings if isinstance(f, dict) and f.get("value") is not None]

        self._findings_cache = {"turns_id": id(turns), "data": valid}
        return valid

    # ------------------------------------------------------------------
    # Private extraction helpers
    # ------------------------------------------------------------------

    def _extract_incident_info(self, turns: list[dict]) -> dict:
        """
        Extract incident date, type, location, and initial injuries from transcript turns.

        Calls _call_llm (which is cached) and maps metadata findings to the dict
        expected by parse():
          incident_date, incident_type, incident_location, injuries (list[str]),
          confidence_scores (dict[str, float]).

        NOTE: The existing function signature accepts turns: list[dict] and returns a
        dict. The task spec describes a findings-list format; that format is the
        internal LLM output. This method transforms it to match the existing interface.
        """
        findings = self._call_llm(turns)

        # Index metadata findings by field name for O(1) lookup
        meta: dict[str, dict] = {
            f["field"]: f
            for f in findings
            if f.get("finding_type") == "metadata"
        }

        # --- incident_date ---
        incident_date: date | None = None
        raw_date = meta.get("accident_date", {}).get("value")
        if raw_date:
            try:
                incident_date = date.fromisoformat(str(raw_date))
            except (ValueError, TypeError):
                logger.warning(
                    "TranscriptParserService: could not parse accident_date %r", raw_date
                )

        # --- incident_type (mapped to Case.IncidentType choices) ---
        incident_type: str | None = None
        raw_case_type = meta.get("case_type", {}).get("value")
        if raw_case_type:
            incident_type = self._INCIDENT_TYPE_MAP.get(
                str(raw_case_type).lower().strip(), "other"
            )

        # --- injuries ---
        raw_injuries = meta.get("injuries", {}).get("value")
        injuries: list[str] = (
            [i.strip() for i in str(raw_injuries).split(",") if i.strip()]
            if raw_injuries
            else []
        )

        return {
            "incident_date": incident_date,
            "incident_type": incident_type,
            "incident_location": meta.get("incident_location", {}).get("value"),
            "injuries": injuries,
            "confidence_scores": {},
        }

    def _extract_parties(self, turns: list[dict]) -> list[dict]:
        """
        Extract other parties (at-fault drivers, property owners, etc.) from turns.

        Returns list of dicts with keys: first_name, last_name, company_name, role.
        Internal keys prefixed with ``_`` carry citation metadata for persist().
        """
        findings = self._call_llm(turns)

        parties: list[dict] = []
        for f in findings:
            if f.get("finding_type") != "individual" or f.get("field") != "other_party":
                continue

            value = str(f.get("value", "")).strip()
            if not value:
                continue

            citation = {
                "_cited_text": f.get("quote") or value,
                "_turn_index": f.get("transcript_index"),
                "_confidence": f.get("confidence", "high"),
            }

            value_lower = value.lower()
            is_company = any(kw in value_lower for kw in self._COMPANY_KEYWORDS)

            if is_company:
                parties.append({
                    "first_name": "",
                    "last_name": "",
                    "company_name": value,
                    "role": "at-fault party",
                    **citation,
                })
            else:
                # Split "First Last" — handle single-name edge case
                parts = value.split(" ", 1)
                first_name = parts[0] if len(parts) == 2 else ""
                last_name = parts[1] if len(parts) == 2 else parts[0]
                parties.append({
                    "first_name": first_name,
                    "last_name": last_name,
                    "company_name": "",
                    "role": "at-fault party",
                    **citation,
                })

        return parties

    def _extract_medical(self, turns: list[dict]) -> list[dict]:
        """
        Extract medical providers and treatments mentioned in turns.

        Returns list of dicts with keys:
          first_name, last_name, facility_name, specialty, treatment_type, diagnosis.
        Internal keys prefixed with ``_`` carry citation metadata for persist().
        """
        findings = self._call_llm(turns)

        medical: list[dict] = []
        for f in findings:
            if f.get("finding_type") != "individual" or f.get("field") != "medical_provider":
                continue

            value = str(f.get("value", "")).strip()
            if not value:
                continue

            citation = {
                "_cited_text": f.get("quote") or value,
                "_turn_index": f.get("transcript_index"),
                "_confidence": f.get("confidence", "high"),
            }

            value_lower = value.lower()
            is_facility = any(kw in value_lower for kw in self._FACILITY_KEYWORDS)

            if is_facility:
                medical.append({
                    "first_name": "",
                    "last_name": "",
                    "facility_name": value,
                    "specialty": "",
                    "treatment_type": "",
                    "diagnosis": "",
                    **citation,
                })
            else:
                # Individual provider — "Dr. First Last" or "First Last"
                name = re.sub(r"^(Dr\.?\s+|Doctor\s+)", "", value, flags=re.IGNORECASE).strip()
                parts = name.split(" ", 1)
                first_name = parts[0] if len(parts) == 2 else ""
                last_name = parts[1] if len(parts) == 2 else parts[0]
                medical.append({
                    "first_name": first_name,
                    "last_name": last_name,
                    "facility_name": "",
                    "specialty": "",
                    "treatment_type": "",
                    "diagnosis": "",
                    **citation,
                })

        return medical

    def _extract_insurance(self, turns: list[dict]) -> list[dict]:
        """
        Extract insurance carrier information from turns.

        Returns list of dicts with keys:
          company_name, policy_number, claim_number, coverage_type, adjuster_name.
        """
        findings = self._call_llm(turns)

        insurance: list[dict] = []
        for f in findings:
            if f.get("finding_type") != "individual" or f.get("field") != "insurance_provider":
                continue

            value = str(f.get("value", "")).strip()
            if not value:
                continue

            insurance.append({
                "company_name": value,
                "policy_number": "",
                "claim_number": "",
                "coverage_type": "liability",
                "adjuster_name": "",
            })

        return insurance

    def _extract_damages(self, turns: list[dict]) -> list[dict]:
        """
        Extract damage claims and amounts from turns.

        Returns list of dicts with keys: category, description, estimated_amount.
        """
        findings = self._call_llm(turns)

        damages: list[dict] = []
        for f in findings:
            if f.get("finding_type") != "individual" or f.get("field") != "financial_expense":
                continue

            value = str(f.get("value", "")).strip()
            if not value:
                continue

            # Try to extract a dollar amount from the description
            amount_match = re.search(r"\$?([\d,]+(?:\.\d+)?)", value)
            estimated_amount: float | None = None
            if amount_match:
                try:
                    estimated_amount = float(amount_match.group(1).replace(",", ""))
                except ValueError:
                    pass

            # Classify by keyword heuristic
            value_lower = value.lower()
            if any(kw in value_lower for kw in ("wage", "lost income", "lost earnings")):
                category = "lost_wages"
            elif "future" in value_lower and "medical" in value_lower:
                category = "future_medical"
            elif any(kw in value_lower for kw in ("medical", "hospital", "doctor", "bill", "treatment")):
                category = "medical"
            elif any(kw in value_lower for kw in ("property", "vehicle", "car", "truck", "repair")):
                category = "property"
            else:
                category = "other"

            damages.append({
                "category": category,
                "description": value,
                "estimated_amount": estimated_amount,
                "_cited_text": f.get("quote") or value,
                "_turn_index": f.get("transcript_index"),
                "_confidence": f.get("confidence", "high"),
            })

        return damages

    def _flag_risks(self, result: IntakeExtractionResult) -> list[str]:
        """
        Analyse an extraction result and return a list of risk flag strings.

        Uses heuristics on the structured result plus keyword scan of raw LLM
        findings to identify flags such as:
          - "statute_of_limitations_risk"
          - "uninsured_motorist"
          - "pre_existing_condition"
          - "multiple_defendants"
          - "liability_disputed"
        """
        flags: list[str] = []

        # Statute of limitations: typical PI SOL is 2 years; flag if > ~20 months old
        if result.incident_date:
            days_since = (date.today() - result.incident_date).days
            if days_since > 600:
                flags.append("statute_of_limitations_risk")

        # Uninsured motorist: auto case with no insurance mentioned
        if result.incident_type == "auto" and not result.insurance_carriers:
            flags.append("uninsured_motorist")

        # Multiple defendants
        if len(result.other_parties) > 1:
            flags.append("multiple_defendants")

        # Keyword scan over all LLM finding values for nuanced signals
        cached = getattr(self, "_findings_cache", None)
        findings = cached["data"] if cached else []
        all_text = " ".join(str(f.get("value", "")) for f in findings).lower()

        if any(kw in all_text for kw in ("pre-existing", "prior injury", "previous condition", "prior condition")):
            flags.append("pre_existing_condition")

        if any(kw in all_text for kw in ("disputed", "dispute", "denied liability", "deny liability", "not at fault")):
            flags.append("liability_disputed")

        return flags
