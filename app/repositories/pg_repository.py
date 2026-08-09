import logging
from contextlib import contextmanager

import psycopg2
from pgvector.psycopg2 import register_vector
from psycopg2.extras import execute_values, register_uuid

# psycopg2는 uuid.UUID를 기본으로 어댑트하지 못한다("can't adapt type 'UUID'").
# 모듈 전역 등록이라 한 번만 호출하면 된다.
register_uuid()

from app.repositories.base import ChunkHit, ChunkScope
from app.repositories.pg_mapper import COLUMNS, to_rows
from app.services.bm25 import BM25Index

logger = logging.getLogger(__name__)

# 검색 결과를 ChunkHit으로 만들 때 필요한 컬럼들.
# id를 chunk_id로 쓴다 - 콜백의 sources[].chunkId가 이 UUID여야 Spring이
# coverage_item_sources를 채울 수 있기 때문이다.
SELECT_FIELDS = """
    id, document_id, chunk_index, page_start, page_end,
    section_title, clause_path, coverage_category, clause_type, content
"""

INSERT_SQL = f"""
INSERT INTO policy_chunks ({", ".join(COLUMNS)})
VALUES %s
ON CONFLICT (analysis_result_id, chunk_index) DO NOTHING
"""

# pgvector의 <=> 는 코사인 거리(0에 가까울수록 유사)라, 유사도로 쓰려면 1에서 뺀다.
SEARCH_SQL = f"""
SELECT {SELECT_FIELDS}, 1 - (embedding <=> %(vector)s::vector) AS score
FROM policy_chunks
WHERE embedding IS NOT NULL
  {{scope}}
ORDER BY embedding <=> %(vector)s::vector
LIMIT %(top_k)s
"""

# related_chunk_id 컬럼이 DDL에 없어서, 면책 페어링을 조회 시점에 다시 계산한다.
# 규칙은 청킹 때와 같다: included 조항 바로 다음이 같은 카테고리의 excluded면 짝이다.
# UNIQUE(analysis_result_id, chunk_index)가 순서를 보장하므로 인덱스 +1로 찾을 수 있다.
RELATED_SQL = f"""
SELECT {SELECT_FIELDS}, 0.0 AS score
FROM policy_chunks nxt
WHERE nxt.analysis_result_id = %(analysis_result_id)s
  AND nxt.chunk_index = %(chunk_index)s + 1
  AND nxt.clause_type = 'excluded'
  -- 카테고리가 둘 다 있고 같을 때만 묶는다. link_exclusion_pairs와 같은 조건이라야
  -- 파일 저장소와 pgvector가 같은 짝을 낸다. IS NOT DISTINCT FROM을 쓰면
  -- NULL끼리도 묶여 파일 쪽보다 짝이 늘어난다(실측 12쌍 대 11쌍).
  AND nxt.coverage_category = %(coverage_category)s
"""


# pgvector 기반 저장소.
#
# FileVectorRepository와 같은 인터페이스를 구현하므로, 이 클래스를 쓰도록 바꿔도
# rag_service와 라우터는 수정할 필요가 없다.
class PgVectorRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @contextmanager
    def _cursor(self, commit: bool = False):
        connection = psycopg2.connect(self._dsn)
        # vector 타입은 확장이 정의한 것이라 연결마다 등록해야 한다.
        # 등록하면 파이썬 리스트를 그대로 넣고 읽을 수 있다.
        register_vector(connection)
        try:
            with connection.cursor() as cursor:
                yield cursor
            if commit:
                connection.commit()
        finally:
            connection.close()

    # ── 색인 ─────────────────────────────────────────────────

    # analysis_result_id와 scope는 분석 요청에서 오므로 save 시그니처만으로는 부족하다.
    # VectorRepository Protocol을 유지하기 위해 컨텍스트를 미리 심어두는 방식을 쓴다.
    def bind(self, analysis_result_id: str, scope: ChunkScope) -> "PgVectorRepository":
        self._analysis_result_id = analysis_result_id
        self._scope = scope
        return self

    def save(self, chunks: list[dict], embeddings: dict[str, list[float]]) -> None:
        if not chunks:
            return

        analysis_result_id = getattr(self, "_analysis_result_id", None)
        scope = getattr(self, "_scope", None)
        if not analysis_result_id or scope is None:
            raise RuntimeError("save() 전에 bind(analysis_result_id, scope)를 호출해야 합니다.")

        rows, id_map = to_rows(chunks, embeddings, analysis_result_id, scope)

        with self._cursor(commit=True) as cursor:
            execute_values(cursor, INSERT_SQL, rows, page_size=200)

        self.last_id_map = id_map
        logger.info("policy_chunks 저장 완료: %d행", len(rows))

    # ── 질의 ─────────────────────────────────────────────────

    def search(
        self,
        query_vector: list[float],
        policy_id: str | None = None,
        top_k: int = 8,
    ) -> list[ChunkHit]:
        scope = "AND policy_id = %(policy_id)s" if policy_id else ""
        sql = SEARCH_SQL.format(scope=scope)

        with self._cursor() as cursor:
            cursor.execute(sql, {"vector": query_vector, "policy_id": policy_id, "top_k": top_k})
            rows = cursor.fetchall()

        return [self._to_hit(row) for row in rows]

    # 키워드 검색은 계약 범위의 청크를 읽어 파이썬 BM25로 돌린다.
    #
    # PostgreSQL 전문검색(tsvector)을 쓰지 않는 이유: 한국어 사전이 없으면 조사가 붙은
    # 어절을 분리하지 못해 품질이 크게 떨어진다. 우리는 이미 조사 변화에 강한
    # 접두 n-gram 토크나이저를 만들어 검증했고, 한 계약의 청크는 수백 개 수준이라
    # 메모리에 올려 계산해도 부담이 없다.
    def search_text(
        self,
        query: str,
        policy_id: str | None = None,
        top_k: int = 8,
    ) -> list[ChunkHit]:
        scope = "WHERE policy_id = %(policy_id)s" if policy_id else ""
        sql = f"SELECT {SELECT_FIELDS}, 0.0 AS score FROM policy_chunks {scope}"

        with self._cursor() as cursor:
            cursor.execute(sql, {"policy_id": policy_id})
            rows = cursor.fetchall()

        if not rows:
            return []

        hits = [self._to_hit(row) for row in rows]
        # 색인 대상은 파일 저장소와 같아야 한다. 임베딩할 때 쓴 텍스트
        # (특약명 + 조항 제목 + 본문)와 일치시켜야 두 저장소가 같은 결과를 낸다.
        index = BM25Index([f"{h.clause_path or ''}\n{h.section_title}\n{h.text}" for h in hits])
        scores = index.scores(query)

        ranked = sorted(zip(hits, scores), key=lambda pair: -pair[1])
        result = []
        for hit, score in ranked[:top_k]:
            if score <= 0:
                break
            hit.score = float(score)
            result.append(hit)
        return result

    # 면책 페어링. FileVectorRepository는 related_chunk_id를 그대로 읽지만,
    # DDL에 그 컬럼이 없으므로 여기서는 chunk_index 인접성으로 다시 찾는다.
    def get_by_ids(self, chunk_ids: list[str]) -> list[ChunkHit]:
        if not chunk_ids:
            return []

        sql = f"SELECT {SELECT_FIELDS}, 0.0 AS score FROM policy_chunks WHERE id = ANY(%(ids)s)"
        with self._cursor() as cursor:
            cursor.execute(sql, {"ids": list(chunk_ids)})
            return [self._to_hit(row) for row in cursor.fetchall()]

    def find_related_exclusion(self, hit: ChunkHit, analysis_result_id: str) -> ChunkHit | None:
        if hit.coverage_type != "included":
            return None

        with self._cursor() as cursor:
            cursor.execute(
                RELATED_SQL,
                {
                    "analysis_result_id": analysis_result_id,
                    "chunk_index": hit.chunk_index,
                    "coverage_category": hit.matched_category,
                },
            )
            row = cursor.fetchone()

        return self._to_hit(row) if row else None

    @staticmethod
    def _to_hit(row: tuple) -> ChunkHit:
        (
            chunk_id,
            document_id,
            chunk_index,
            page_start,
            page_end,
            section_title,
            clause_path,
            coverage_category,
            clause_type,
            content,
            score,
        ) = row

        hit = ChunkHit(
            chunk_id=str(chunk_id),
            document_id=str(document_id),
            page_start=page_start or 0,
            page_end=page_end or 0,
            section_title=section_title or "",
            coverage_type=clause_type,
            text=content,
            matched_category=coverage_category,
            related_chunk_id=None,
            score=float(score),
        )
        hit.chunk_index = chunk_index
        hit.clause_path = clause_path
        return hit
