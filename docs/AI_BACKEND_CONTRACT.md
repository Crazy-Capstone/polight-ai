# 백엔드 ↔ AI 통신 규격 (현재 구현 기준)

코드에 실제로 구현된 것만 적는다. 앞으로 하기로 한 것과 합의가 남은 것은 8절에 따로 모았다.
근거 파일을 각 절에 달아뒀으니, 이 문서와 코드가 어긋나면 코드가 맞다.

**요약**: 통신은 네 갈래다.

| 방향 | 무엇 | 경로 |
|---|---|---|
| 백엔드 → AI | 분석 요청 접수 | `POST /internal/analysis` → 202 |
| 백엔드 → AI | 챗봇 질의 | `POST /internal/rag/query` → 200 |
| AI → 백엔드 | 분석 완료 콜백 | `POST /internal/analysis-results/{id}/complete` |
| AI → 백엔드 | 분석 실패 콜백 | `POST /internal/analysis-results/{id}/fail` |

---

## 1. 공통 규칙

| 항목 | 값 | 근거 |
|---|---|---|
| JSON 키 표기 | **camelCase** (양방향 전부) | `app/schemas/base.py` |
| 인증 헤더 | `X-Internal-Api-Key: <INTERNAL_API_KEY>` | `app/core/auth.py` |
| 인증 범위 | `/internal/*` 전체. `GET /health`만 무인증 | `app/api/router.py` |
| 키 미설정 시 | 요청을 통과시키고 경고 로그만 남긴다(로컬 개발용). `SPRING_BASE_URL`이 설정된 환경에서 키가 없으면 **기동 실패** | `app/core/auth.py`, `app/main.py` |
| 모르는 필드 | 요청에 스키마에 없는 필드가 있어도 **무시하고 처리한다**(pydantic 기본 동작). 400/422가 아니다 | `app/schemas/base.py` |
| 콜백 타임아웃 | 10초 | `app/clients/spring_client.py` |

AI가 보내는 콜백에도 같은 헤더를 실어 보낸다. 요청 수신용 키와 콜백 발신용 키가 같은 값이다.

`snake_case`로 보내도 받는다(`populate_by_name=True`). 다만 정식 계약은 camelCase다.

---

## 2. 백엔드 → AI: 분석 요청 접수

```
POST /internal/analysis
X-Internal-Api-Key: <키>
Content-Type: application/json
```

```json
{
  "analysisResultId": "a1b2c3d4-...",
  "documentId": "d1e2f3...",
  "userId": "u1...",
  "tripId": "t1...",
  "policyId": null,
  "downloadUrl": "https://s3.../certificate.pdf",
  "documentType": "CERTIFICATE"
}
```

| 필드 | 타입 | 필수 | 비고 |
|---|---|---|---|
| `analysisResultId` | string | O | 콜백 URL의 `{id}`로 그대로 되돌아온다 |
| `documentId` | string | O | 내려받은 PDF 파일명, 청크 스코프에 쓰인다 |
| `userId` | string | O | |
| `tripId` | string | O | |
| `policyId` | string \| null | X | 저장은 하지만 검색 스코프로 쓰지 않는다 |
| `downloadUrl` | string | O | **`fileUrl` / `download_url`로 보내도 받는다** |
| `documentType` | `"TERMS"` \| `"CERTIFICATE"` \| null | X | 없으면 페이지 수로 판별(10p 이하 → CERTIFICATE) |

근거: `app/schemas/analysis.py:AnalysisStartRequest`, `app/services/analysis_service.py:_resolve_document_type`

**응답 (202 Accepted)** — 접수 확인만 한다. 실제 처리는 백그라운드다.

```json
{ "analysisResultId": "a1b2c3d4-...", "status": "ACCEPTED" }
```

`status`는 항상 `"ACCEPTED"` 리터럴이다. 결과는 4·5절의 콜백으로만 온다.

**PDF 내려받기 동작** (`_download_pdf`)

- 내부 API 키가 설정돼 있으면 항상 `X-Internal-Api-Key` 헤더를 실어 보낸다. presigned URL은 서명에 없는 헤더를 무시하므로 S3든 백엔드 엔드포인트든 그대로 동작한다.
- 리다이렉트를 따라간다, 타임아웃 60초.
- 최대 3회 재시도(2초, 4초). **4xx는 재시도하지 않는다**(주소 오류·만료·권한).
- 최종 실패는 실패 콜백(5절)으로 나간다.

---

## 3. 백엔드 → AI: 챗봇 질의

```
POST /internal/rag/query
```

```json
{
  "userId": "u1...",
  "tripId": "t1...",
  "documentId": "carrot_travel_2025",
  "question": "휴대품 도난 한도가 얼마예요?",

  "policyId": null,
  "sessionId": "s1...",
  "history": [
    { "sender": "USER",      "content": "해외에서 다치면 보상되나요?" },
    { "sender": "ASSISTANT", "content": "해외의료비 보장으로..." }
  ],

  "clausePaths": ["해외여행중 휴대품손해 특별약관"],
  "coverages": [
    { "name": "해외여행중 휴대품손해(분실제외)", "subscribed": true,
      "limitAmount": 2000000, "limitCurrency": "KRW" },
    { "name": "해외여행중 항공기 및 수하물 지연비용", "subscribed": false }
  ],
  "coveragesComplete": true
}
```

| 필드 | 타입 | 필수 | 기본값 | 역할 |
|---|---|---|---|---|
| `userId` | string | O | | |
| `tripId` | string | O | | `documentId`가 없을 때 검색 스코프 |
| `question` | string | O | | |
| `documentId` | string \| null | X | null | 있으면 그 약관만 검색 |
| `policyId` | string \| null | X | null | **검색 필터로 쓰지 않는다** (항상 null로 오고, SQL `= NULL`은 0건이 된다) |
| `sessionId` | string \| null | X | null | 없으면 단발 질의 |
| `history` | HistoryTurn[] | X | `[]` | 최근 3턴(6개) 권장. AI는 `chat_messages`를 직접 조회하지 않는다 |
| `clausePaths` | string[] | X | `[]` | 검색을 해당 특약으로 좁힌다 |
| `coverages` | CertificateCoverage[] | X | `[]` | 프롬프트에 실려 가입 여부·한도 판단 근거가 된다 |
| `coveragesComplete` | boolean | X | `false` | `coverages`가 증권 보장내용 표 **전체**일 때만 `true` |

`HistoryTurn`: `sender` = `USER` \| `ASSISTANT` \| `SYSTEM` (DB CHECK 값), `content` = string.

`CertificateCoverage`: `name`(필수), `subscribed`(기본 `true`), `limitAmount`(int \| null), `limitCurrency`(string \| null).
개인정보는 필드 자체를 두지 않았다 — 이름·생년월일·증권번호는 받지 않는다.

근거: `app/schemas/rag.py`

**응답 (200 OK)**

```json
{
  "answer": "휴대품 손해는 1개 또는 1조당 20만원 한도로...",
  "responseType": "TEXT",
  "sources": [
    { "chunkId": "c-uuid", "documentId": "carrot_travel_2025",
      "page": 78, "quote": "약관 원문 인용..." }
  ]
}
```

| 필드 | 타입 | 비고 |
|---|---|---|
| `answer` | string | 근거가 없으면 고정 문구를 보낸다(아래) |
| `responseType` | enum | 스키마는 `TEXT`/`HOSPITAL_CARDS`/`COVERAGE_CARDS`/`EMERGENCY_CONTACTS`/`POLICY_SUMMARY`. **현재 구현은 항상 `TEXT`** |
| `sources` | SourceChunk[] | LLM 생성물이 아니라 검색된 청크 원문을 잘라 만든다. `page`는 조항 시작 페이지 |

근거 0건일 때: `answer`에 `"제공된 약관에서 관련 근거를 찾을 수 없습니다. ..."`가 들어가고 `sources`는 `[]`다. HTTP는 그래도 200이다. (`app/services/rag_service.py:NO_EVIDENCE_ANSWER`)

---

## 4. AI → 백엔드: 분석 완료 콜백

```
POST {SPRING_BASE_URL}/internal/analysis-results/{analysisResultId}/complete
X-Internal-Api-Key: <키>
```

경로는 환경변수 `CALLBACK_COMPLETE_PATH`로 바꿀 수 있다(`{id}` 자리에 `analysisResultId`).
`SPRING_BASE_URL`이 비어 있으면 **전송하지 않고 경고 로그만 남긴다**.

```json
{
  "analysisResultId": "a1b2c3d4-...",
  "status": "COMPLETED",
  "summary": "보장 항목 21개를 확인했습니다: 해외의료비 보장 상해, ... 외 18건",
  "coverageItems": [ /* 아래 4-1 */ ],

  "embeddingModel": "upstage-1536",
  "embeddingDimension": 1536,
  "rawResultJson": "{...}",

  "insurerName": "한화손해보험(주)",
  "productName": "해외여행자보험"
}
```

| 필드 | 타입 | 비고 |
|---|---|---|
| `analysisResultId` | string | 요청의 값 그대로 |
| `status` | `"COMPLETED"` | 고정 리터럴 |
| `summary` | string | 화면에 노출되는 유일한 분석 텍스트. `"보장 항목 N개를 확인했습니다: A, B, C 외 N건"` 형태. 0건이면 `"약관에서 보장 항목을 찾지 못했습니다..."` |
| `coverageItems` | CoverageItemPayload[] | 배열 순서 = 화면 표시 순서. `sortOrder`는 보내지 않는다(Spring이 인덱스로 부여) |
| `embeddingModel` | string \| null | 재색인 판단용 |
| `embeddingDimension` | int \| null | 현재 1536 |
| `rawResultJson` | string \| null | **JSON 문자열**(객체 아님). 파싱하지 말고 TEXT로 보관 |
| `insurerName` | string \| null | 증권 분석일 때만 채워진다 |
| `productName` | string \| null | 증권 분석일 때만. `product_name` 없으면 `document_title`로 대체 |

근거: `app/schemas/analysis.py:AnalysisCompleteCallback`, `app/services/analysis_service.py`

### 4-1. `coverageItems[]` 한 건

```json
{
  "title": "해외의료비 보장 상해",
  "coverageStatus": "COVERED",
  "subtitle": null,
  "category": "해외의료비 보장",
  "limitLabel": "US 5만달러",
  "limitAmount": 50000,
  "limitCurrency": "USD",
  "conditions": "여행 중 상해로 해외에서 의료비 발생시 실제 부담한 의료비 보상",

  "detailItems": [
    { "title": "치료비", "subtitle": null, "isCovered": true }
  ],
  "subLimits": [
    { "label": "1개 또는 1조당", "value": "20만원",
      "limitAmount": 200000, "limitCurrency": "KRW", "description": null }
  ],
  "requiredDocuments": [
    { "documentName": "진단서", "isMandatory": true }
  ],
  "exclusions": [
    { "title": "치과치료", "description": "...", "sourceText": "약관 원문",
      "severity": "WARNING" }
  ],
  "sources": [
    { "chunkId": "policy_chunks.id UUID", "sourceRole": "PRIMARY",
      "quoteText": "약관 원문 인용" }
  ]
}
```

`isCovered`는 **보내지 않는다.** `coverageStatus IN (COVERED, PARTIALLY_COVERED)`로 Spring이 파생한다.
`limitLabel`과 `limitAmount`를 둘 다 보내는 이유: `limitAmount`는 BIGINT라 정수만 들어가는데 증권에는 `"US 5만달러"`, `"(정액) 50만원"`처럼 통화·지급방식이 붙어 있다. 화면에는 `limitLabel` 원문이 정확하다.

`sources[].chunkId`는 AI가 `policy_chunks` INSERT 때 만든 UUID다. 그래서 저장 → 콜백 순서를 지킨다. 반대면 `coverage_item_sources`의 FK가 깨진다. `UNIQUE(coverageItemId, policyChunkId, sourceRole)` 위반을 막기 위해 AI 쪽에서 (chunk, role) 중복을 이미 제거해 보낸다.

### 4-2. enum 허용값 (전송 시점에 이 값으로 번역해 보낸다)

| 필드 | 허용값 |
|---|---|
| `coverageStatus` | `COVERED` \| `PARTIALLY_COVERED` \| `NOT_COVERED` \| `EXCLUDED` |
| `sources[].sourceRole` | `PRIMARY` \| `CONDITION` \| `EXCLUSION` \| `LIMIT` \| `PROCEDURE` \| `REQUIRED_DOCUMENT` \| `DEFINITION` |
| `exclusions[].severity` | `GENERAL` \| `WARNING` \| `CRITICAL` |

내부값 → 전송값 번역표(`app/schemas/db_enums.py`). 모르는 값이 와도 예외를 내지 않고 fallback으로 보낸다.

| 내부 | 전송 | | 내부 | 전송 |
|---|---|---|---|---|
| `PARTIAL` | `PARTIALLY_COVERED` | | `COVERAGE` | `PRIMARY` |
| `UNKNOWN` | `NOT_COVERED` | | `DOCUMENT` | `REQUIRED_DOCUMENT` |
| `LOW`/`MEDIUM`/`HIGH` | `GENERAL`/`WARNING`/`CRITICAL` | | (미매핑) | `PRIMARY` / `NOT_COVERED` / `WARNING` |

### 4-3. 길이 컷 (VARCHAR 초과로 INSERT가 실패하는 것을 막는다)

`title` 200 / `subtitle` 500 / `category` 100 / `limitLabel` 100 / `limitCurrency` 10 /
`documentName` 200 / `subLimits.label` 100 / `subLimits.value` 200 / `description` 500

`conditions`, `exclusions.description`, `exclusions.sourceText`, `quoteText`는 TEXT 컬럼이라 자르지 않는다.
근거: `app/services/callback_mapper.py:MAX_LENGTHS`

---

## 5. AI → 백엔드: 분석 실패 콜백

```
POST {SPRING_BASE_URL}/internal/analysis-results/{analysisResultId}/fail
```

```json
{
  "analysisResultId": "a1b2c3d4-...",
  "status": "FAILED",
  "errorMessage": "약관 PDF를 내려받지 못했습니다 (https://...): 404 Not Found"
}
```

`status`는 고정 `"FAILED"`. `errorMessage`는 파이썬 예외 메시지 원문이라 형식이 정해져 있지 않다. 화면에 그대로 띄우지 말고 로그·디버깅용으로 쓰는 편이 낫다.

실패 콜백이 나가는 경우: PDF 내려받기 실패, 파싱·청킹·임베딩·저장 실패, 증권 에이전트 실패(담보 0건 포함).
경로 환경변수는 `CALLBACK_FAIL_PATH`.

---

## 6. 콜백 재시도와 멱등성

| 항목 | 값 |
|---|---|
| 최대 시도 | 3회 |
| 백오프 | 2초 → 4초 (지수) |
| 재시도하는 응답 | 408, 429, 500, 502, 503, 504, 그리고 연결 실패·타임아웃 |
| 재시도하지 않는 응답 | 그 외 4xx (400 스키마 불일치, 401 인증 실패 등) |

**중복 수신이 발생할 수 있으니 `analysisResultId` 기준 멱등 처리가 필요하다.**

3회 다 실패하면 AI 쪽은 에러 로그만 남기고 넘어간다. 분석 파이프라인 자체는 성공으로 두기 때문에 **재전송 큐가 없다** — 콜백이 유실되면 `analysis_results.status`가 `PROCESSING`에 고착된다. Spring의 청크 조회가 `status = COMPLETED`로 필터하므로, 데이터는 다 들어갔는데 챗봇만 0건이 되는 조용한 고장이 된다.

근거: `app/clients/spring_client.py`, `tests/test_callback_retry.py`

---

## 7. 경로별로 실제 채워지는 값 (중요)

같은 완료 콜백이지만 약관 경로와 증권 경로에서 채워지는 필드가 다르다.

| 필드 | 약관(TERMS) | 증권(CERTIFICATE) |
|---|---|---|
| `summary` | O | O |
| `coverageItems[].title` / `coverageStatus` | O | O |
| `limitLabel` / `limitAmount` / `limitCurrency` | 대개 비어 있다 (약관은 "보험가입금액을 한도로"라고만 적혀 있어, 실측 30건 중 3건만 채워졌다) | **O** (실측 21/21) |
| `subtitle` / `category` | O | O (증권의 2단 분류) |
| `conditions` | O | O (보장내용 설명 표에서) |
| `detailItems` / `subLimits` / `requiredDocuments` / `exclusions` | O | **항상 `[]`** |
| `sources` | O (`policy_chunks` UUID) | **항상 `[]`** (증권은 청킹·임베딩하지 않는다) |
| `embeddingModel` / `embeddingDimension` | O | **null** |
| `rawResultJson` | 보낸 `coverageItems` 배열의 JSON | Upstage 에이전트 원본 출력 JSON |
| `insurerName` / `productName` | **null** | O |

증권 경로는 `coverageStatus`를 금액 유무로 정한다 — 금액이 파싱되면 `COVERED`, 연령대 컬럼이 `"-"`면 `NOT_COVERED`(+ `limitLabel`은 `"보장하지 않음"`). 미보장 담보도 카드로 만들어 보낸다. 화면에서 "미보장"으로 보이는 편이 목록에서 빠져 존재를 모르는 것보다 낫다고 봤다.

증권에서 나온 담보 목록은 3절의 `coverages`로 다시 들어오는 값과 같은 출처다(`certificate_adapter.to_coverages`). 증권 PDF는 분석 직후 디스크에서 지운다(피보험자 이름·생년월일·증권번호).

근거: `app/services/certificate_adapter.py`, `app/services/analysis_service.py:_process_certificate`

**알려진 구멍**: 4-3의 길이 컷은 약관 경로(`callback_mapper.to_payload`)에만 걸려 있다. 증권 경로는 어댑터가 payload를 직접 만들어 컷을 타지 않는다. 증권 담보명·설명은 짧아 실측에서 문제가 없었지만, 긴 설명이 오면 Spring 쪽 INSERT가 VARCHAR 초과로 실패할 수 있다.

---

## 8. 오류 응답 (AI가 요청을 거절할 때)

| 상태 | 언제 | 바디 |
|---|---|---|
| 401 | 헤더 없음 또는 키 불일치 | `{"detail": "유효한 내부 API 키가 필요합니다."}` — 어느 쪽이 틀렸는지는 알려주지 않는다 |
| 422 | 필수 필드 누락·타입 오류 | FastAPI 기본 검증 오류 형식 (`{"detail": [{"loc": ..., "msg": ..., "type": ...}]}`) |
| 500 | 처리되지 않은 예외 | `{"detail": "Internal server error"}` — 내부 정보를 흘리지 않는다 |

`GET /health` → `{"status": "ok"}` (무인증, 로드밸런서용)

---

## 9. 아직 구현되지 않은 것 / 합의가 남은 것

| 항목 | 현재 | 남은 일 |
|---|---|---|
| 챗봇 요청의 약관 지정 | `documentId`에 AI가 들고 있는 약관 식별자(예: `carrot_travel_2025`)를 넣어 보낸다 | `policy_terms` 테이블이 생기면 `termsId` 필드를 추가한다 |
| `/internal/terms/match` | 없다. 증권↔약관 매칭은 AI 내부 참조 구현(`terms_matcher.py`)만 있다 | 백엔드가 Java로 옮기지 않겠다면 엔드포인트로 열어준다 |
| `responseType` 카드형 4종 | 스키마에만 있고 항상 `TEXT`를 보낸다 | 렌더링 화면이 생기면 사용 |
| 콜백 재전송 큐 | 없다(3회 실패 후 포기) | 필요해지면 |
| 피보험자 연령 | 증권 금액 컬럼을 고를 때 30세로 가정한다 | 백엔드가 생년월일로 계산해 넘기면 정확해진다 |
| 백엔드의 `fail-on-unknown-properties` | 확인 필요 | 켜져 있으면 `insurerName`/`productName`에서 400이 난다 |

`SPRING_BASE_URL` / `INTERNAL_API_KEY`가 없으면 콜백은 전송되지 않고 로그만 남는다. 콜백 경로·형식 검증은 `scripts/mock_spring.py`로 한다(경로 오타를 잡기 위해 등록되지 않은 POST도 경고로 남긴다).
