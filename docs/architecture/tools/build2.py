# -*- coding: utf-8 -*-
from gen import *

d = Diagram('AI 파이프라인', 1680, 770)
d.node('title', 'text;html=1;align=left;verticalAlign=middle;fontSize=20;fontStyle=1;fontColor=#1A365D;',
       40, 8, 700, 32, 'Polight — AI 파이프라인')
d.node('subtitle', 'text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#4A5568;',
       42, 34, 900, 18, '증권 분석 · 약관 색인 · 챗봇 RAG — 세 흐름의 트리거와 응답 방식이 다르다')

BADGE = ('shape=image;imageAspect=0;aspect=fixed;html=1;labelPosition=center;verticalLabelPosition=middle;'
         'verticalAlign=middle;noLabel=1;image=%s;')

def lane(nid, x, y, w, h, title, stroke, fill, font):
    d.node(nid, st_plain_group(stroke, fill, font), x, y, w, h, title)

def steps(items, x0, y, w, gap, kind, parent):
    """items: (id, label, badge|None). 박스 위에 기술 로고 배지를 올린다."""
    ids = []
    for i, (nid, label, badge) in enumerate(items):
        x = x0 + i * (w + gap)
        if badge:
            if badge.startswith('aws:'):
                d.node(nid + '_b', st_aws_res(badge[4:]) + 'noLabel=1;', x + w // 2 - 14, y - 34, 28, 28, '', parent)
            else:
                d.node(nid + '_b', BADGE % logo_uri(badge), x + w // 2 - 14, y - 34, 28, 28, '', parent)
        d.node(nid, kind, x, y, w, 76, label, parent)
        ids.append(nid)
    for a, b in zip(ids, ids[1:]):
        d.edge(a, b, '', EDGE, exit=('1', '0.5'), entry=('0', '0.5'))
    return ids

BOX_IN   = st_box('#1565C0', '#EAF3FF', '#0D47A1')   # 백엔드와의 접점
BOX_PROC = st_box('#7B1FA2', '#F8EEFF', '#4A148C')   # AI 서버 내부 처리
BOX_VEN  = st_box('#EF6C00', '#FFF4E5', '#E65100')   # 외부 벤더 호출
BOX_DATA = st_box('#2E7D32', '#EAF7EC', '#1B5E20')   # 저장소

# ── 1. 증권 분석 ───────────────────────────────────────────────
lane('l1', 40, 70, 1600, 190, '1. 증권 분석 — 사용자 업로드 · 비동기 · 수십 초 · DB 미사용',
     '#1565C0', '#FBFDFF', '#0D47A1')
steps([('s1', '<b>Spring → AI</b><br>POST /internal/analysis<br>analysisResultId · documentId', 'spring'),
       ('s2', '<b>202 Accepted</b><br>즉시 응답 후<br>BackgroundTasks 로 계속', None),
       ('s3', '<b>증권 PDF 수신</b><br>S3 Presigned GET', 'aws:s3'),
       ('s4', '<b>Upstage Studio Agent</b><br>파싱 → 분류 → 추출<br>담보 · 한도 · 보험기간', None),
       ('s5', '<b>certificate_adapter</b><br>담보 payload 변환', 'python'),
       ('s6', '<b>category_mapping</b><br>표준 카테고리 8종 환산', None),
       ('s7', '<b>terms_matcher</b><br>증권 ↔ 보유 약관 매칭', None),
       ('s8', '<b>Spring 콜백</b><br>PUT /internal/<br>analysis-results/{id}', 'spring'),
       ('s9', '<b>증권 PDF 삭제</b><br>피보험자 정보 보호', None)],
      54, 168, 150, 28, BOX_PROC, 'l1')
for n, style in [('s1', BOX_IN), ('s2', BOX_IN), ('s3', BOX_VEN), ('s4', BOX_VEN),
                 ('s8', BOX_IN), ('s9', BOX_IN)]:
    next(x for x in d.nodes if x['id'] == n)['style'] = style

# ── 2. 약관 색인 ───────────────────────────────────────────────
lane('l2', 40, 300, 1050, 190, '2. 약관 색인 — 운영자 사전 작업 · 오프라인 · 1건 4~5분',
     '#2E7D32', '#FBFEFB', '#1B5E20')
steps([('p1', '<b>약관 PDF</b><br>보험사 공시자료', None),
       ('p2', '<b>ingest_terms.py</b><br>Upstage Document Parse<br>청킹 → 임베딩', 'python'),
       ('p3', '<b>migrate_terms_to_db.py</b><br>청크 → policy_terms_chunks', 'python'),
       ('p4', '<b>migrate_terms_coverages.py</b><br>coverage_extractor (LLM)<br>→ policy_terms_coverages', 'python')],
      54, 398, 228, 32, BOX_PROC, 'l2')
next(x for x in d.nodes if x['id'] == 'p1')['style'] = BOX_DATA

# ── 3. 챗봇 RAG ────────────────────────────────────────────────
lane('l3', 40, 530, 1600, 190, '3. AI 챗봇 — 실시간 RAG · 동기 응답 · Spring read timeout 60s',
     '#7B1FA2', '#FDFAFF', '#4A148C')
steps([('r1', '<b>Spring → AI</b><br>POST /internal/rag/query<br>질문 · termsId · 이력 6개', 'spring'),
       ('r2', '<b>query_rewriter</b><br>검색용 질문 재작성', 'python'),
       ('r3', '<b>Upstage Embedding</b><br>upstage-1536', None),
       ('r4', '<b>하이브리드 검색</b><br>pgvector 코사인 + BM25', 'postgresql'),
       ('r5', '<b>MMR reranker</b><br>중복 제거 · 다양성', None),
       ('r6', '<b>prompt_builder</b><br>근거 + 대화 이력', None),
       ('r7', '<b>answer_providers</b><br>OpenAI GPT-4.1 ·<br>Gemini · Claude', 'gemini'),
       ('r8', '<b>답변 + 약관 근거</b><br>근거 0건이면 정형 문구', None)],
      54, 628, 172, 32, BOX_PROC, 'l3')
for n, style in [('r1', BOX_IN), ('r3', BOX_VEN), ('r4', BOX_DATA), ('r7', BOX_VEN), ('r8', BOX_IN)]:
    next(x for x in d.nodes if x['id'] == n)['style'] = style

# ── 공용 저장소 · 표준 카테고리 ─────────────────────────────────
d.node('db', st_logo('postgresql'), 1215, 355, 56, 56,
       '<b>PostgreSQL 16 + pgvector</b><br>policy_terms_chunks<br>policy_terms_coverages')
d.node('cat', st_box('#2E7D32', '#EAF7EC', '#1B5E20'), 1360, 340, 260, 96,
       '<b>표준 보장 카테고리 8종</b><br>의료비 · 항공지연 · 수하물 · 긴급이송<br>'
       '치과응급 · 배상책임 · 여행취소 · 사망후유장해<br>— 증권 담보와 약관 규칙을 잇는 공통 키')

d.edge('p3', 'db', '적재', EDGE, exit=('1', '0.5'), entry=('0', '0.25'))
d.edge('p4', 'db', '적재', EDGE, exit=('1', '0.5'), entry=('0', '0.75'))
d.edge('db', 'r4', '약관 청크 조회', EDGE, exit=('0.5', '1'), entry=('0.5', '0'))
d.edge('cat', 's6', '표준 카테고리로 환산', EDGE_DASH, exit=('0', '0.5'), entry=('0.5', '1'))
d.edge('cat', 'p4', '보장 규칙에도 같은 키', EDGE_DASH, exit=('0', '0.75'), entry=('1', '0.25'), lpos='-0.7')

if __name__ == '__main__':
    open('preview2.svg', 'w').write(d.preview())
    print('ok')
