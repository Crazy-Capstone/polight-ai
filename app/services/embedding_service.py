from openai import OpenAI

from app.core.config import get_settings
from app.services.embedding_providers import build_client, embed, get_provider
from scripts.embed_chunks import BATCH_SIZE, build_embed_text


# 벤더는 settings.embedding_provider로 고른다.
#
# 운영은 Solar Embedding 2를 사용한다. API 원본은 1024차원이며,
# 저장된 문서 벡터와 DB vector(1536) 규격에 맞게 0을 패딩한다.
def _provider():
    return get_provider(get_settings().embedding_provider)


# 청크를 임베딩한다 (문서 쪽).
#
# Upstage는 질의와 문서에 서로 다른 모델을 쓰는 비대칭 임베딩이라,
# 여기서는 반드시 is_query=False로 호출해야 한다. 섞으면 벡터 공간이 어긋나
# 검색이 사실상 무작위가 된다.
def embed_chunks(
    chunks: list[dict],
    client: OpenAI | None = None,
    model: str | None = None,
) -> dict[str, list[float]]:
    provider = _provider()
    client = client or build_client(provider)

    results: dict[str, list[float]] = {}
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        texts = [build_embed_text(c) for c in batch]
        vectors = embed(provider, texts, is_query=False, client=client)
        for chunk, vector in zip(batch, vectors):
            results[chunk["chunk_id"]] = vector

    return results


# 질문 하나를 벡터로 변환한다 (질의 쪽).
def embed_query(
    question: str,
    client: OpenAI | None = None,
    model: str | None = None,
) -> list[float]:
    provider = _provider()
    client = client or build_client(provider)
    return embed(provider, [question], is_query=True, client=client)[0]
