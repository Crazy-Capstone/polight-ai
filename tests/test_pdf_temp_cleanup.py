"""Temporary PDF lifecycle tests for the analysis pipeline."""

import pytest

from app.core.config import Settings
from app.schemas.analysis import AnalysisCompleteCallback, AnalysisStartRequest
from app.services import analysis_service
from app.services.analysis_errors import AnalysisFailure


REQUEST = AnalysisStartRequest.model_validate({
    "analysisResultId": "analysis-temp-1",
    "documentId": "document-temp-1",
    "userId": "user-temp-1",
    "tripId": "trip-temp-1",
    "downloadUrl": "https://storage.test/document.pdf?signature=secret",
})


def test_partial_download_failure_removes_request_workspace(monkeypatch, tmp_path):
    temp_root = tmp_path / "pdf-temp"
    monkeypatch.setattr(analysis_service, "PDF_TEMP_ROOT", temp_root)
    monkeypatch.setattr(analysis_service, "get_settings", lambda: Settings())

    class BrokenStream:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"partial PDF data"
            raise RuntimeError("stream failed")

    monkeypatch.setattr(analysis_service.httpx, "stream", lambda *_args, **_kwargs: BrokenStream())

    with pytest.raises(AnalysisFailure):
        analysis_service._download_pdf(REQUEST.download_url, REQUEST.document_id)

    assert list(temp_root.iterdir()) == []


def test_preclassification_exception_removes_downloaded_pdf(monkeypatch, tmp_path):
    temp_root = tmp_path / "pdf-temp"
    workspace = temp_root / "request-one"
    workspace.mkdir(parents=True)
    pdf_path = workspace / "document.pdf"
    pdf_path.write_bytes(b"temporary PDF")
    monkeypatch.setattr(analysis_service, "PDF_TEMP_ROOT", temp_root)
    monkeypatch.setattr(analysis_service, "_download_pdf", lambda *_args: pdf_path)
    monkeypatch.setattr(analysis_service, "_unlock_or_reject", lambda *_args: None)
    monkeypatch.setattr(
        analysis_service,
        "_resolve_document_type",
        lambda *_args: (_ for _ in ()).throw(ValueError("classification failed")),
    )
    failed = []
    monkeypatch.setattr(analysis_service, "_safe_notify_fail", lambda *_args: failed.append(True))

    analysis_service.process_analysis(REQUEST, repository=object())

    assert not workspace.exists()
    assert failed == [True]


def test_certificate_callback_runs_only_after_temp_workspace_is_removed(monkeypatch, tmp_path):
    temp_root = tmp_path / "pdf-temp"
    workspace = temp_root / "request-one"
    workspace.mkdir(parents=True)
    pdf_path = workspace / "document.pdf"
    pdf_path.write_bytes(b"temporary PDF")
    monkeypatch.setattr(analysis_service, "PDF_TEMP_ROOT", temp_root)
    monkeypatch.setattr(analysis_service, "_download_pdf", lambda *_args: pdf_path)
    monkeypatch.setattr(analysis_service, "_unlock_or_reject", lambda *_args: None)
    monkeypatch.setattr(analysis_service, "_resolve_document_type", lambda *_args: "CERTIFICATE")
    callback = AnalysisCompleteCallback(
        analysisResultId=REQUEST.analysis_result_id,
        summary="ok",
        coverageItems=[],
    )
    monkeypatch.setattr(
        analysis_service,
        "_process_certificate",
        lambda *_args, **_kwargs: callback,
    )
    observed = []

    def notify_complete(_callback):
        observed.append(workspace.exists())

    monkeypatch.setattr(analysis_service.spring_client, "notify_complete", notify_complete)

    analysis_service.process_analysis(REQUEST, repository=object())

    assert observed == [False]


def test_successful_terms_pdf_is_archived_but_temp_copy_is_removed(monkeypatch, tmp_path):
    temp_root = tmp_path / "pdf-temp"
    raw_pdf_dir = tmp_path / "raw-pdfs"
    workspace = temp_root / "request-one"
    workspace.mkdir(parents=True)
    pdf_path = workspace / "document.pdf"
    pdf_path.write_bytes(b"terms PDF")
    monkeypatch.setattr(analysis_service, "PDF_TEMP_ROOT", temp_root)
    monkeypatch.setattr(analysis_service, "RAW_PDF_DIR", raw_pdf_dir)
    monkeypatch.setattr(analysis_service, "_download_pdf", lambda *_args: pdf_path)
    monkeypatch.setattr(analysis_service, "_unlock_or_reject", lambda *_args: None)
    monkeypatch.setattr(analysis_service, "_resolve_document_type", lambda *_args: "TERMS")
    monkeypatch.setattr(analysis_service, "parse_and_chunk", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(analysis_service, "embed_chunks", lambda *_args: {})

    class Repo:
        def save(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(analysis_service, "_safe_notify_complete", lambda *_args: None)

    analysis_service.process_analysis(REQUEST, repository=Repo())

    assert (raw_pdf_dir / f"{REQUEST.document_id}.pdf").read_bytes() == b"terms PDF"
    assert not workspace.exists()
