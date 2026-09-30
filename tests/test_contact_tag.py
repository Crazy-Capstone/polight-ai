"""사고 정황 판단 결과(연락처 태그) 파싱.

태그는 LLM과 우리 서버 사이의 내부 장치다. 사용자에게 보이면 안 되고, 태그 때문에
답변이 깨지거나 응답 검증이 터져서도 안 된다. 답변은 이미 만들어졌는데 전달에
실패하는 것이 가장 아깝다.
"""

from app.services.rag_service import split_contact_tag


class TestExtract:
    def test_한_종류(self):
        answer, contacts = split_contact_tag("경찰에 신고하세요. [[CONTACT: POLICE]]")
        assert answer == "경찰에 신고하세요."
        assert contacts == ["POLICE"]

    def test_여러_종류(self):
        _, contacts = split_contact_tag("다쳤고 도난도 당하셨군요. [[CONTACT: HOSPITAL, POLICE]]")
        assert contacts == ["HOSPITAL", "POLICE"]

    def test_NONE은_빈_목록(self):
        answer, contacts = split_contact_tag("최대 500만원까지 보상됩니다. [[CONTACT: NONE]]")
        assert answer == "최대 500만원까지 보상됩니다."
        assert contacts == []

    def test_태그가_없으면_원문_그대로(self):
        # 모델이 지시를 잊는 경우다. 기존 동작(연락처 없음)으로 떨어져야 한다.
        answer, contacts = split_contact_tag("최대 500만원까지 보상됩니다.")
        assert answer == "최대 500만원까지 보상됩니다."
        assert contacts == []

    def test_중복은_한_번만(self):
        _, contacts = split_contact_tag("...[[CONTACT: POLICE]]...[[CONTACT: POLICE]]")
        assert contacts == ["POLICE"]


class TestSanitize:
    def test_본문_중간에_써도_지운다(self):
        # 마지막 줄만 보면 이 경우 태그가 사용자 화면에 그대로 보인다.
        answer, contacts = split_contact_tag("도난은 [[CONTACT: POLICE]] 보상됩니다.")
        assert "CONTACT" not in answer
        assert contacts == ["POLICE"]

    def test_태그가_남긴_빈_줄을_정리한다(self):
        answer, _ = split_contact_tag("보상됩니다.\n\n[[CONTACT: POLICE]]\n")
        assert answer == "보상됩니다."

    def test_공백이_섞인_태그도_읽는다(self):
        _, contacts = split_contact_tag("...[[ CONTACT : hospital , police ]]")
        assert contacts == ["HOSPITAL", "POLICE"]

    def test_스키마에_없는_값은_버린다(self):
        # Literal 검증에서 500이 나면 답변 자체가 전달되지 않는다.
        _, contacts = split_contact_tag("...[[CONTACT: AMBULANCE, POLICE]]")
        assert contacts == ["POLICE"]

    def test_태그만_있으면_원문을_쓴다(self):
        # 빈 답변을 내보내는 것보다 낫다.
        answer, contacts = split_contact_tag("[[CONTACT: POLICE]]")
        assert answer == "[[CONTACT: POLICE]]"
        assert contacts == ["POLICE"]


class TestResponseContract:
    def test_응답에_실려_나간다(self, client, fake_repo, monkeypatch):
        from app.services import rag_service
        from tests.conftest import make_hit

        fake_repo.hits = [make_hit("c1")]
        monkeypatch.setattr(rag_service, "embed_query", lambda q, client=None: [0.1] * 1536)
        monkeypatch.setattr(
            rag_service,
            "_call_llm",
            lambda *a, **kw: "도난은 개당 20만원까지 보상됩니다. [[CONTACT: POLICE]]",
        )

        response = client.post(
            "/internal/rag/query",
            json={
                "userId": "u1",
                "tripId": "t1",
                "documentId": "doc-1",
                "termsId": "terms-1",
                "question": "지갑을 도난당했는데 보상되나요?",
            },
        )
        assert response.status_code == 200
        body = response.json()

        assert body["suggestedContacts"] == ["POLICE"]
        assert "CONTACT" not in body["answer"]
        # responseType은 TEXT로 둔다. 바꾸면 프론트가 텍스트 대신 카드를 그릴 수 있고,
        # 그러면 약관 답변이 사라진다.
        assert body["responseType"] == "TEXT"
        # 약관 답변이 그대로 살아 있어야 한다. 섞인 질문이 예외가 아니라 기본이다.
        assert "20만원" in body["answer"]

    def test_기본값은_빈_목록(self, client, fake_repo, monkeypatch):
        from app.services import rag_service
        from tests.conftest import make_hit

        fake_repo.hits = [make_hit("c1")]
        monkeypatch.setattr(rag_service, "embed_query", lambda q, client=None: [0.1] * 1536)
        monkeypatch.setattr(rag_service, "_call_llm", lambda *a, **kw: "최대 500만원까지 보상됩니다.")

        response = client.post(
            "/internal/rag/query",
            json={
                "userId": "u1",
                "tripId": "t1",
                "documentId": "doc-1",
                "termsId": "terms-1",
                "question": "의료비 한도가 얼마예요?",
            },
        )
        assert response.json()["suggestedContacts"] == []
