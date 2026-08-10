from typing import Literal

from app.schemas.base import CamelModel


class SourceChunk(CamelModel):
    chunk_id: str
    document_id: str
    page: int
    quote: str


# 대화 이력 1턴.
#
# Spring이 chat_messages에서 최근 6개(3턴)를 잘라 실어 보낸다. Python이 DB를 조회하지
# 않는 이유는 두 가지다. rag_service 계정에 chat_messages SELECT 권한을 주면 AI 서버가
# 전체 사용자 대화 전문에 접근하게 되고, 무상태로 두면 요청 페이로드만으로 재현되므로
# 디버깅이 쉽다.
#
# sender는 DB CHECK 제약값이라 이 셋만 허용된다.
class HistoryTurn(CamelModel):
    sender: Literal["USER", "ASSISTANT", "SYSTEM"]
    content: str


# POST /internal/rag/query 요청 바디
class RagQueryRequest(CamelModel):
    user_id: str
    trip_id: str
    question: str

    # 검색 범위. document_id가 있으면 그 약관만, 없으면 trip_id로 여행 단위로 넓힌다.
    #
    # policy_id로 필터하지 않는다. 백엔드에 policies 행을 만드는 코드가 없어 이 값이
    # 항상 null로 오고, SQL에서 "= NULL"은 아무 행과도 일치하지 않아 검색이 0건이 된다.
    # 필드는 남겨두되 스코프로 쓰지 않는다.
    document_id: str | None = None
    policy_id: str | None = None

    # 멀티턴 대화용. 없으면 단발 질의로 동작한다.
    session_id: str | None = None
    history: list[HistoryTurn] = []


# POST /internal/rag/query 응답 바디
#
# responseType은 chat_messages.response_type(NOT NULL)에 그대로 들어간다.
# 카드형 4종(HOSPITAL_CARDS 등)은 아직 렌더링하는 화면이 없어 항상 TEXT를 보낸다.
class RagQueryResponse(CamelModel):
    answer: str
    response_type: Literal[
        "TEXT", "HOSPITAL_CARDS", "COVERAGE_CARDS", "EMERGENCY_CONTACTS", "POLICY_SUMMARY"
    ] = "TEXT"
    sources: list[SourceChunk]
