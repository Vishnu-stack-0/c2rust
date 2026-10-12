import logging
import time
from .llm_service import translate_c_to_rust_with_context
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie

from .llm_service import translate_c_to_rust_with_context
from .models import RepairAttempt, TranslationJob
from .repair_service import (
    build_compile_problem,
    build_output_mismatch_problem,
    repair_rust_code,
)
from .verifier import ENVIRONMENT_RUST_STAGES, verify

logger = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 3


@ensure_csrf_cookie
def index(request):
    return render(request, "translator/index.html")


def history(request):
    jobs = TranslationJob.objects.order_by("-created_at")[:50]
    return render(request, "translator/history.html", {"jobs": jobs})


def job_detail(request, job_id):
    job = get_object_or_404(TranslationJob, id=job_id)
    repairs = job.repairs.order_by("attempt_number")
    return render(
        request,
        "translator/job_detail.html",
        {"job": job, "repairs": repairs},
    )


def _get_rag_context(c_code: str):
    """Returns (context, labels, error). Never raises."""
    try:
        from .chroma_rag import retrieve_for_translation
        return retrieve_for_translation(c_code)
    except Exception as exc:
        logger.exception("RAG setup failed")
        return "", [], f"{type(exc).__name__}: {exc}"

def _should_stop_repairing(report: dict) -> bool:
    """Stop when the problem is the machine's toolchain, not the code."""
    if report.get("verification_status") == "verification_environment_error":
        return True
    if report.get("rust_stage") in ENVIRONMENT_RUST_STAGES:
        return True
    return False


def _log_stage(job_id, stage, start):
    print(f"[job {job_id}] {stage}: {time.perf_counter() - start:.1f}s", flush=True)


def api_translate(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST request required."}, status=405)

    c_code = request.POST.get("c_code", "").strip()
    test_input = request.POST.get("test_input")

    if not c_code:
        return JsonResponse({"error": "C code is required."}, status=400)

    job = TranslationJob.objects.create(
        c_code=c_code,
        model_name="groq",
        status="pending",
    )
    print(f"\n[job {job.id}] started", flush=True)
    t_total = time.perf_counter()

    try:
        t = time.perf_counter()
        rag_context, retrieved_titles, rag_error = _get_rag_context(c_code)
        _log_stage(job.id, "RAG lookup", t)

        t = time.perf_counter()
        rust_code = translate_c_to_rust_with_context(c_code, rag_context)
        _log_stage(job.id, "Groq translation", t)

        job.rust_code = rust_code
        job.save()

        t = time.perf_counter()
        report = verify(c_code=c_code, rust_code=rust_code, test_input=test_input)
        _log_stage(job.id, "verify #1", t)
        print(f"[job {job.id}] status: {report.get('verification_status')}", flush=True)

        attempt = 0
        while (
            not report.get("functionally_equivalent_for_test")
            and attempt < MAX_REPAIR_ATTEMPTS
            and not _should_stop_repairing(report)
        ):
            if not report.get("rust_compiled"):
                reason = "compile_error"
                problem = build_compile_problem(report)
            else:
                reason = "output_mismatch"
                problem = build_output_mismatch_problem(report)

            t = time.perf_counter()
            new_rust_code = repair_rust_code(
                c_code=c_code,
                rust_code=rust_code,
                problem=problem,
            )
            _log_stage(job.id, f"Groq repair #{attempt + 1}", t)

            if new_rust_code.strip() == rust_code.strip():
                print(f"[job {job.id}] repair returned same code, stopping", flush=True)
                break

            attempt += 1
            RepairAttempt.objects.create(
                job=job,
                attempt_number=attempt,
                reason=reason,
                old_rust_code=rust_code,
                new_rust_code=new_rust_code,
            )
            rust_code = new_rust_code
            job.rust_code = rust_code
            job.repair_attempts = attempt
            job.save()

            t = time.perf_counter()
            report = verify(c_code=c_code, rust_code=rust_code, test_input=test_input)
            _log_stage(job.id, f"verify #{attempt + 1}", t)
            print(f"[job {job.id}] status: {report.get('verification_status')}", flush=True)

        job.verification_report = report
        job.status = (
            "completed"
            if report.get("functionally_equivalent_for_test")
            else "verification_failed"
        )
        job.save()

        _log_stage(job.id, "TOTAL", t_total)

        return JsonResponse({
            "job_id": job.id,
            "status": job.status,
            "rust_code": rust_code,
            "repair_attempts": attempt,
            "rag_used": bool(retrieved_titles),
            "retrieved_examples": retrieved_titles,
            "rag_error": rag_error,
            "verification_report": report,
        })

    except Exception as exc:
        logger.exception("Translation job %s failed", job.id)
        job.status = "failed"
        job.error_message = str(exc)
        job.save()
        return JsonResponse(
            {"job_id": job.id, "status": "failed", "error": str(exc)},
            status=500,
        )