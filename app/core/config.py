from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "polight-ai-rag"
    api_prefix: str = "/internal"

    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"

    # 임베딩 벤더. app/services/embedding_providers.py의 PROVIDERS 키.
    # 비교 실험 결과 upstage-1536 채택 (MRR 0.7875 vs openai-small 0.5147).
    embedding_provider: str = "upstage-1536"

    # 답변 생성용 채팅 모델. 임베딩 모델과 별개로 관리한다.
    llm_model: str = "gpt-4o-mini"

    # Upstage 문서 파싱. heading 계층과 요소 타입을 제공해
    # policy_chunks의 clause_path와 source_content_type(NOT NULL)을 채운다.
    upstage_api_key: str = ""

    # 임베딩 모델 비교용. 없으면 해당 벤더는 비교에서 자동으로 빠진다.
    qwen_api_key: str = ""

    # 검색 시 가져올 청크 수. related_chunk_id로 딸려오는 면책 조항은 이 수에 포함되지 않는다.
    top_k: int = 8

    # MMR 재순위 설정.
    # 약관은 같은 표준 조항이 특약마다 반복돼, 유사도 정렬만으로는 top_k가 중복본으로 채워진다.
    # 후보를 top_k의 배수만큼 넉넉히 뽑은 뒤 다양성을 고려해 top_k로 줄인다.
    mmr_candidate_multiplier: int = 4
    mmr_lambda: float = 0.6

    # DB 스키마 확정 전까지는 비워둠. 내일 연결 시 값 채우면 repository 구현체가 사용.
    database_url: str | None = None

    # Spring 콜백 대상 (완료/실패 알림). 아직 미확정이면 비워둔 채로 로컬 테스트.
    spring_base_url: str | None = None

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
