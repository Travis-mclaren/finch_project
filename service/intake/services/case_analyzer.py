"""
case_analyzer.py — LLM-backed case analysis service.

Public interface:
    analyze_case(case_id) -> dict

Raises:
    Case.DoesNotExist   — propagated directly; caller maps to 404
    CaseAnalysisError   — LLM call failure or unparseable response
"""

from __future__ import annotations

import json
import logging

import openai
from django.conf import settings

logger = logging.getLogger(__name__)


class CaseAnalysisError(Exception):
    """Raised when the LLM call fails or returns unparseable output."""


# ---------------------------------------------------------------------------
# System prompt — reproduced verbatim per specification; do not modify.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior legal case analyst AI assistant at a personal injury law firm. You are
given structured data about a legal case pulled from the firm's case management system.
Your job is to analyze this data and produce a structured case evaluation report.

Be precise and conservative. Do NOT invent, assume, or extrapolate facts not present in
the data. If a section lacks sufficient data to make a confident assessment, say so
explicitly and set confidence to "low". Never fabricate dollar amounts, dates, or names.

Produce a JSON report with exactly these five sections:

1. incident_summary
   A concise narrative (3-5 sentences) describing what happened based solely on the
   data provided. Include: who was involved, what occurred, when and where it happened,
   and any immediately relevant context. Do not editorialize.

2. damages_summary
   A structured account of all damages. For each item include:
   - damage_type      : category of damage (medical, property, lost_wages, pain, other)
   - description      : what the damage is
   - amount           : dollar amount as a number, or null if unknown/not provided
   - provider         : associated medical provider name if applicable, or null
   Cover all Treatment and Damages model data. If a treatment has no dollar amount,
   still include it with amount: null.

3. liability_summary
   An assessment of who the most likely liable party is based on the available data.
   Include:
   - liable_party     : name of the individual or entity most likely at fault
   - basis            : the specific facts from the data that support this assessment
   - confidence       : "high", "medium", or "low"
   - caveats          : any missing information that could change this assessment, or null

4. insurance_summary
   An account of all insurance involvement. For each insurer include:
   - provider_name    : name of the insurance company
   - policy_type      : type of coverage (auto, health, liability, umbrella, unknown)
   - related_to       : "client", "other_party", or "unknown"
   - claim_number     : claim number if available, or null
   - notes            : any relevant context about this insurer's role in the case

5. case_viability
   A structured assessment of whether the law firm should take on this case.
   - recommendation   : one of: "strong_yes", "yes", "neutral", "no", "strong_no"
   - viability_score  : integer 0–100 representing likelihood firm should take the case.
                        weigh information that is populated as more significant when determining
                        this score.  heavily weigh if the liability_summary result is empty or if 
                        there is no obvious entity at fault.
   - reasoning        : array of specific strings, each one a discrete factor that
                        contributed to this score (both positive and negative factors)
   - missing_info     : array of strings describing information gaps that, if filled,
                        would most change this assessment
   - red_flags        : array of strings for any concerns that lower the viability_score, or []

Return ONLY valid JSON. No markdown. No keys outside this schema."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _na(value) -> str:
    """Return the value as a string, or 'N/A' if falsy (None / empty string)."""
    if value is None or value == "":
        return "N/A"
    return str(value)


def _build_user_message(case) -> str:
    """
    Assemble the structured user message from the fully-prefetched Case object.

    Every section is always present; sections with no data write
    "No data available." rather than being omitted.
    """
    client = case.client
    other_parties = list(case.other_parties.all())
    treatments = list(case.treatments.all())
    damages = list(case.damages.all())
    communications = list(case.communications.all())

    # Collect insurance: client's own policies first, then per other-party
    insurers: list[tuple[str, object]] = []
    for ip in client.insurance_providers.all():
        insurers.append(("client", ip))
    for op in other_parties:
        for ip in op.insurance_providers.all():
            insurers.append(("other_party", ip))

    lines: list[str] = []

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    lines += [
        "CASE ANALYSIS REQUEST",
        "=====================",
        f"Case ID: {case.id}",
        f"Case Number: {_na(case.case_number)}",
        f"Case Type: {case.get_incident_type_display()}",
        f"Status: {case.get_status_display()}",
        f"Incident Date: {_na(case.incident_date)}",
        f"Incident Location: {_na(case.incident_location)}",
        f"Statute of Limitations Date: {_na(case.statute_of_limitations_date)}",
        "",
    ]

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------
    lines += [
        "CLIENT",
        "------",
        f"Name: {client.first_name} {client.last_name}".strip(),
        f"Phone: {_na(client.phone)}",
        f"Email: {_na(client.email)}",
        f"Address: {_na(client.address)}",
        f"Date of Birth: {_na(client.date_of_birth)}",
        f"Law Firm: {_na(client.law_firm.name)}",
        "",
    ]

    # ------------------------------------------------------------------
    # Incident description
    # ------------------------------------------------------------------
    lines += [
        "INCIDENT DESCRIPTION",
        "--------------------",
        case.description.strip() if case.description.strip() else "No data available.",
        "",
    ]

    # ------------------------------------------------------------------
    # Other parties
    # ------------------------------------------------------------------
    lines += ["OTHER PARTIES", "-------------"]
    if other_parties:
        for op in other_parties:
            name = (
                op.company_name
                or f"{op.first_name} {op.last_name}".strip()
                or "Unknown"
            )
            lines += [
                f"- Name: {name}",
                f"  Role: {_na(op.role)}",
                f"  Phone: {_na(op.phone)}",
                f"  Email: {_na(op.email)}",
                f"  Address: {_na(op.address)}",
            ]
    else:
        lines.append("No data available.")
    lines.append("")

    # ------------------------------------------------------------------
    # Insurance coverage
    # ------------------------------------------------------------------
    lines += ["INSURANCE COVERAGE", "------------------"]
    if insurers:
        for related_to, ip in insurers:
            lines += [
                f"- Provider: {ip.company_name}",
                f"  Coverage Type: {ip.get_coverage_type_display()}",
                f"  Related To: {related_to}",
                f"  Policy Number: {_na(ip.policy_number)}",
                f"  Claim Number: {_na(ip.claim_number)}",
                f"  Policy Limit: {_na(ip.policy_limit)}",
                f"  Adjuster: {_na(ip.adjuster_name)}",
            ]
    else:
        lines.append("No data available.")
    lines.append("")

    # ------------------------------------------------------------------
    # Treatments & medical providers
    # ------------------------------------------------------------------
    lines += ["TREATMENTS & MEDICAL PROVIDERS", "-------------------------------"]
    if treatments:
        for t in treatments:
            if t.provider:
                provider_name = (
                    f"Dr. {t.provider.first_name} {t.provider.last_name}".strip()
                )
                if t.provider.facility:
                    provider_name += f" ({t.provider.facility.name})"
                specialty = _na(t.provider.specialty)
            else:
                provider_name = "Unknown provider"
                specialty = "N/A"
            lines += [
                f"- Provider: {provider_name}",
                f"  Specialty: {specialty}",
                f"  Treatment Type: {_na(t.treatment_type)}",
                f"  Diagnosis: {_na(t.diagnosis)}",
                f"  Start Date: {_na(t.start_date)}",
                f"  End Date: {_na(t.end_date)}",
                f"  Billed Amount: {_na(t.billed_amount)}",
                f"  Paid Amount: {_na(t.paid_amount)}",
                f"  Notes: {_na(t.notes)}",
            ]
    else:
        lines.append("No data available.")
    lines.append("")

    # ------------------------------------------------------------------
    # Damages
    # ------------------------------------------------------------------
    lines += ["DAMAGES", "-------"]
    if damages:
        for d in damages:
            lines += [
                f"- Category: {d.get_category_display()}",
                f"  Description: {_na(d.description)}",
                f"  Estimated Amount: {_na(d.estimated_amount)}",
                f"  Documented: {d.documented}",
                f"  Notes: {_na(d.notes)}",
            ]
    else:
        lines.append("No data available.")
    lines.append("")

    # ------------------------------------------------------------------
    # Client communications (up to 10 transcript turns per communication)
    # ------------------------------------------------------------------
    lines += ["CLIENT COMMUNICATIONS", "---------------------"]
    if communications:
        for comm in communications:
            lines += [
                f"- Channel: {comm.get_channel_display()}",
                f"  Occurred At: {_na(comm.occurred_at)}",
                f"  Summary: {_na(comm.summary)}",
            ]
            turns = (
                comm.raw_transcript[:10]
                if isinstance(comm.raw_transcript, list)
                else []
            )
            if turns:
                lines.append("  Transcript (first 10 turns):")
                for turn in turns:
                    speaker = turn.get("speaker", "Unknown")
                    text = turn.get("text", "")
                    lines.append(f"    [{speaker}]: {text}")
    else:
        lines.append("No data available.")
    lines.append("")

    # ------------------------------------------------------------------
    # Citations & evidence
    # ------------------------------------------------------------------
    lines += ["CITATIONS & EVIDENCE", "--------------------"]
    citations = [cit for comm in communications for cit in comm.citations.all()]
    if citations:
        for cit in citations:
            lines += [
                f"- Key: {cit.citation_key}",
                f"  Cited Text: {_na(cit.cited_text)}",
                f"  Turn Index: {_na(cit.turn_index)}",
                f"  Confidence: {cit.confidence_score:.2f}",
            ]
            if cit.notes:
                lines.append(f"  Notes: {cit.notes}")
    else:
        lines.append("No data available.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def analyze_case(case_id) -> dict:
    """
    Fetches all relevant case data, calls the OpenAI API, and returns a
    structured case analysis report.

    NOTE: The contract specifies ``case_id: int`` but Case.id is a UUID field.
    The parameter accepts any value Django can use as a primary-key lookup
    (str UUID, uuid.UUID instance, etc.).

    Args:
        case_id: Primary key of the Case record to analyze.

    Returns:
        A dict matching the five-section schema defined in the system prompt.

    Raises:
        Case.DoesNotExist: If no case with this ID exists.
        CaseAnalysisError: If the LLM call fails or returns unparseable output.
    """
    from intake.models import Case  # deferred to avoid circular import

    case = (
        Case.objects.select_related(
            "client",
            "client__law_firm",
        )
        .prefetch_related(
            "other_parties",
            "other_parties__insurance_providers",
            "client__insurance_providers",
            "treatments",
            "treatments__provider",
            "treatments__provider__facility",
            "damages",
            # "communications",
            "communications__citations",
        )
        .get(pk=case_id)
    )

    user_message = _build_user_message(case)

    api_key = getattr(settings, "OPENAPI_KEY", None)
    if not api_key:
        raise CaseAnalysisError(
            "OPENAPI_KEY is not set. Add it to your .env file and ensure "
            "load_dotenv() is called in settings.py."
        )

    oai_client = openai.OpenAI(api_key=api_key)

    try:
        response = oai_client.chat.completions.create(
            model="gpt-5",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
    except openai.OpenAIError as exc:
        logger.error(
            "OpenAI API call failed in analyze_case (case %s): %s", case_id, exc
        )
        raise CaseAnalysisError(f"LLM call failed: {exc}") from exc

    raw = response.choices[0].message.content
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "analyze_case: could not parse LLM response as JSON (case %s). "
            "Raw content: %.500s",
            case_id,
            raw,
        )
        raise CaseAnalysisError(f"LLM returned non-JSON response: {exc}") from exc

    # Validate required top-level sections
    required = {
        "incident_summary",
        "damages_summary",
        "liability_summary",
        "insurance_summary",
        "case_viability",
    }
    missing = required - report.keys()
    if missing:
        logger.error(
            "analyze_case: LLM response missing required sections %s (case %s)",
            missing,
            case_id,
        )
        raise CaseAnalysisError(
            f"LLM response missing required sections: {missing}"
        )

    # Enforce viability_score is an integer 0–100
    viability = report.get("case_viability", {})
    score = viability.get("viability_score")
    if not isinstance(score, int):
        try:
            viability["viability_score"] = max(0, min(100, int(score)))
        except (TypeError, ValueError):
            viability["viability_score"] = 0

    return report
