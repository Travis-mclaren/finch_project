"""
HTML (non-API) views for the intake app.

Kept separate from views.py to avoid mixing DRF viewsets with Django template views.
"""

from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .models import Case
from .services.case_analyzer import CaseAnalysisError, analyze_case


def case_summary(request, case_id: str):
    """
    Render a human-readable case analysis summary.

    URL: GET /api/v1/cases/<uuid:case_id>/summary/

    Fetches the Case (for client identity), calls analyze_case() to generate
    the LLM report, then passes everything to the case_summary template.

    Context:
        case_id     — UUID of the case (str)
        client_name — "<first> <last>" from Case.client
        analysis    — dict from analyze_case() with five sections:
                        incident_summary, damages_summary, liability_summary,
                        insurance_summary, case_viability
    """
    case = get_object_or_404(
        Case.objects.select_related("client"),
        pk=case_id,
    )

    try:
        analysis = analyze_case(case_id)
    except CaseAnalysisError as exc:
        # Surface LLM failures as a 500 with a minimal error page rather than
        # letting Django's default 500 handler swallow the detail.
        return render(
            request,
            "intake/case_summary_error.html",
            {"error": str(exc), "case_id": case_id},
            status=500,
        )

    client = case.client
    context = {
        "case_id": case.id,
        "client_name": f"{client.first_name} {client.last_name}".strip(),
        "analysis": analysis,
    }
    return render(request, "intake/case_summary.html", context)
