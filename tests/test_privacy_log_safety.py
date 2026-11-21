"""개인정보 원문이 애플리케이션 로그와 예외 메시지에 남지 않는지 확인한다."""

from types import SimpleNamespace

import httpx
import pytest

from app.clients import spring_client
from app.core.config import Settings
from app.schemas.analysis import AnalysisStartRequest
from app.services import analysis_service, certificate_analyzer, query_rewriter
from app.services.analysis_errors import AnalysisFailure
from app.services.certificate_analyzer import CertificateAnalysisError


def test_query_rewriter_logs_do_not_contain_question_or_rewrite(caplog, monkeypatch):
    question_secret = "QUESTION-PRIVATE-9382"
    history_secret = "HISTORY-PRIVATE-2716"
    rewrite_secret = "REWRITE-PRIVATE-6401"
    history = [{"role": "user", "content": history_secret}]

    monkeypatch.setattr(
        query_rewriter, "generate", lambda *args, **kwargs: (rewrite_secret, 0.2)
    )
    caplog.set_level("INFO", logger="app.services.query_rewriter")

    result = query_rewriter.rewrite(f"그럼 {question_secret} 얼마예요?", history)

    assert result == rewrite_secret
    assert question_secret not in caplog.text
    assert history_secret not in caplog.text
    assert rewrite_secret not in caplog.text


def test_spring_callback_logs_do_not_contain_payload_or_url(caplog, monkeypatch):
    payload_secret = "CALLBACK-PRIVATE-5512"
    url_secret = "PRESIGNED-PRIVATE-7703"
    monkeypatch.setattr(
        spring_client,
        "get_settings",
        lambda: Settings(spring_base_url="http://spring.test", internal_api_key=""),
    )

    def fail_post(url, **kwargs):
        request = httpx.Request("POST", url)
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError(url_secret, request=request, response=response)

    monkeypatch.setattr(spring_client.httpx, "post", fail_post)
    caplog.set_level("INFO", logger="app.clients.spring_client")

    with pytest.raises(spring_client.SpringCallbackError) as exc_info:
        spring_client._post(
            "/internal/complete",
            {"insuredName": payload_secret},
        )

    assert payload_secret not in caplog.text
    assert url_secret not in caplog.text
    assert payload_secret not in str(exc_info.value)
    assert url_secret not in str(exc_info.value)


def test_certificate_parse_error_does_not_include_ai_response():
    response_secret = "AI-RESPONSE-PRIVATE-4820"
    job = SimpleNamespace(id="job-safe-id", output_text=response_secret)

    with pytest.raises(CertificateAnalysisError) as exc_info:
        certificate_analyzer._read_output(job)

    assert response_secret not in str(exc_info.value)
    assert exc_info.value.__suppress_context__ is True


def test_pdf_download_error_does_not_include_presigned_url(caplog, monkeypatch, tmp_path):
    url_secret = "https://storage.test/file.pdf?signature=URL-PRIVATE-3219"
    monkeypatch.setattr(analysis_service, "PDF_TEMP_ROOT", tmp_path / "pdf_temp")
    monkeypatch.setattr(analysis_service, "get_settings", lambda: Settings())
    monkeypatch.setattr(analysis_service.time, "sleep", lambda _: None)

    def fail_stream(*args, **kwargs):
        raise httpx.ConnectError(url_secret)

    monkeypatch.setattr(analysis_service.httpx, "stream", fail_stream)
    caplog.set_level("WARNING", logger="app.services.analysis_service")

    with pytest.raises(AnalysisFailure) as exc_info:
        analysis_service._download_pdf(url_secret, "document-safe-id")

    assert url_secret not in caplog.text
    assert url_secret not in str(exc_info.value)
    assert list((tmp_path / "pdf_temp").iterdir()) == []


def test_request_shape_log_does_not_include_unexpected_values(caplog):
    private_values = [
        "TERMS-PRIVATE-8341",
        "COVERAGE-PRIVATE-2947",
        "COMPLETE-PRIVATE-5680",
    ]
    caplog.set_level("INFO", logger="app.api.routes.rag")

    from app.api.routes.rag import _log_request_shape

    _log_request_shape(
        {
            "termsId": private_values[0],
            "coverages": [{"name": private_values[1]}],
            "coveragesComplete": private_values[2],
        }
    )

    for private_value in private_values:
        assert private_value not in caplog.text
