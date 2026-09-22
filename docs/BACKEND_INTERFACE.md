# AI 서버 ↔ 백엔드 연동 정리

증권 기반으로 전환하면서 필요한 것과 제공하는 것을 정리한 문서다.

**핵심 원칙: 백엔드가 지금 아무것도 바꾸지 않아도 그대로 동작한다.**
새로 추가하는 필드는 전부 선택이고, 없으면 지금과 똑같이 돈다.
백엔드가 여유 될 때 하나씩 채우면 그만큼 정확해진다.

---

## 0. 지금 당장 필요한 것 (3개)

이게 없으면 연동 자체가 안 된다. 나머지는 전부 나중에 해도 된다.

| # | 필요한 것 | 들어갈 곳 (AI `.env`) | 왜 |
|---|---|---|---|
| 1 | **내부 API 키 값** | `INTERNAL_API_KEY` | AI가 콜백을 보낼 때, 백엔드가 AI를 호출할 때 양쪽이 같은 값을 쓴다. 지금 비어 있어 인증이 꺼져 있다 |
| 2 | **백엔드 Base URL** | `SPRING_BASE_URL` | 콜백을 보낼 주소. 미설정이라 콜백을 건너뛰고 로그만 남긴다 |
| 3 | **파일 전달 방식** | (코드 대응) | S3 presigned URL인지, 백엔드가 파일을 내려주는 엔드포인트인지 |

`SPRING_BASE_URL`을 설정하면 `INTERNAL_API_KEY`가 없을 때 서버가 기동을 거부한다.
콜백을 보내는 환경에서 인증이 빠지는 것을 막으려는 장치라, 둘은 같이 와야 한다.

3번은 AI 쪽이 이미 양쪽 다 대응해뒀다. 키가 있으면 항상 헤더에 실어 보내고,
presigned URL은 서명에 없는 헤더를 무시하므로 있어도 무해하다. **알려만 주면 된다.**

### AI 서버 배포 시 함께 필요한 값 (백엔드와 무관, 참고용)

```
UPSTAGE_API_KEY           약관 파싱·임베딩·증권 분석 공용
UPSTAGE_AGENT_ID          증권 분석 에이전트 (agt_로 시작). 없으면 증권 경로가 꺼진다
UPSTAGE_AGENT_API_KEY     에이전트가 다른 콘솔 계정에 있을 때만. 비우면 위 키를 쓴다
ANSWER_PROVIDER           답변 생성 벤더. 비우면 기본값
```

---

## 1. AI 서버가 제공하는 API

인증: 모든 요청에 헤더 `X-Internal-Api-Key: <키>`

### 1-1. 분석 요청 접수

```
POST /internal/analysis
→ 202 Accepted (즉시 응답, 실제 처리는 백그라운드)
```

```json
{
  "analysisResultId": "uuid",
  "documentId": "uuid",
  "userId": "uuid",
  "tripId": "uuid",
  "policyId": "uuid",
  "downloadUrl": "https://.../file.pdf",

  "documentType": "CERTIFICATE"
}
```

- `downloadUrl` 대신 `fileUrl`로 보내도 받는다
- `policyId`는 없어도 된다 (현재 항상 null로 오고 있다)
- **`documentType`이 이번에 추가되는 유일한 필드다.** `TERMS`(약관) / `CERTIFICATE`(증권)
  - **안 보내도 된다.** 없으면 페이지 수로 자동 판별한다 (증권 1~2p, 약관 100p+)
  - 보내주면 더 정확하다

### 1-2. 챗봇 질의

```
POST /internal/rag/query
→ 200 OK
```

```json
{
  "userId": "uuid",
  "tripId": "uuid",
  "documentId": "uuid",
  "question": "휴대품 도난 한도가 얼마예요?",

  "sessionId": "uuid",
  "history": [
    { "sender": "USER", "content": "..." },
    { "sender": "ASSISTANT", "content": "..." }
  ],

  "coverages": [
    { "name": "해외여행중 휴대품손해(분실제외)", "subscribed": true,
      "limitAmount": 2000000, "limitCurrency": "KRW" },
    { "name": "해외여행중 항공기 및 수하물 지연비용", "subscribed": false }
  ],
  "coveragesComplete": true
}
```

응답:

```json
{
  "answer": "도난 시 **개당 최대 20만원**까지 보상됩니다 [근거 1]. 다만 분실은 보상되지 않습니다 [근거 3].",
  "responseType": "TEXT",
  "suggestedContacts": ["POLICE"],
  "sources": [
    {
      "index": 1,
      "chunkId": "uuid",
      "documentId": "uuid",
      "termsId": "uuid",
      "sectionTitle": "제1조(보상하는 손해)",
      "page": 78,
      "pageStart": 78,
      "pageEnd": 79,
      "clauseType": "COVERAGE",
      "text": "제1조(보상하는 손해)\n회사는 피보험자가 여행 중 휴대품에 손해가 생긴 경우...",
      "quote": "제1조(보상하는 손해) 회사는 피보험자가...",
      "cited": true
    }
  ]
}
```

#### 근거 본문 — 프론트가 [근거 N]을 눌러 조항을 볼 수 있게 하는 데 필요한 것

`text`(조항 전문)와 `index`(근거 번호)를 새로 싣는다. **기존 필드는 하나도 빼지
않았으니 DTO를 고치기 전에도 지금과 똑같이 동작한다.**

| 필드 | 설명 |
| --- | --- |
| `index` | 답변 본문의 `[근거 N]`과 잇는 번호(1부터). 배열 순서에 의존하지 말고 이 값으로 매칭해 주세요 |
| `termsId` | 약관 식별자. **`documentId`에도 같은 값이 실린다** — 공용 약관 청크는 문서가 아니라 약관에 매여 있어, 그 값으로 `policy_documents`를 조회하면 0건이다. 기존 계약 호환을 위해 `documentId`는 남겨두고 제 이름으로도 보낸다 |
| `sectionTitle` | 조항 제목. 본문만 띄우면 사용자가 약관 어디를 보는지 알 수 없다 |
| `pageStart` / `pageEnd` | 페이지 범위. `page`는 `pageStart`와 같은 값(기존 필드) |
| `clauseType` | `COVERAGE` / `EXCLUSION` / `PROCEDURE` / `DEFINITION` / `GENERAL`. `policy_terms_chunks.clause_type`의 CHECK 제약값과 같은 어휘다 |
| `text` | 조항 전문. 팝업에 띄울 본문 |
| `cited` | 답변이 실제로 인용했는지. 검색 8건에 짝지어진 면책 조항이 따라붙어 근거가 12건까지 늘어나는데 모델은 일부만 쓴다 |

**`clauseType`이 `EXCLUSION`인 근거는 화면에서 구분돼야 한다.** UI 취향이 아니라
안전 문제다. "보상하지 않는 손해" 조항을 보상 근거로 읽으면, 답변은 맞는데 근거를
열어보고 반대로 이해하는 사고가 난다. 배지 문구도 "면책"보다 **"보상하지 않음"**처럼
풀어쓰기를 권한다.

**페이로드가 커진다.** 약관 7종 2,548청크 실측으로 청크 평균 779자, p90 1,685자,
최대 2,000자다. 근거 12건이 붙은 답변이 **평균 약 27KB, p90 약 59KB**다. 응답
1회로는 문제없지만 `chat_messages.metadata_json`에 그대로 저장하면 같은 용량이
메시지마다 쌓인다. 부담되면 `text`를 빼고 저장한 뒤 조회 시 `chunkId`로
`policy_terms_chunks.content`를 JOIN해 채우는 방법도 있다. 다만 **약관 개정으로
청크가 갈리면 사용자가 그때 본 것과 다른 본문이 뜬다.** 보험 상담 기록이라
저희는 통째로 저장하는 쪽을 권하지만, 판단은 백엔드에 맡긴다.

##### 답변 본문의 `[근거 N]`을 읽는 규칙 (프론트용)

프롬프트가 `[근거 N]` 형식을 지시하지만 모델이 늘 그대로 쓰지는 않는다. 실제 출력에서
확인된 형태는 네 가지다.

```
[근거 1]                            대부분
[근거 1, 2, 4, 8]                   숫자 나열
[근거 1, 근거 2, 근거 3]             "근거" 반복
[근거 1 제2조 4호, 근거 7 제2조 4호]  조항 번호가 딸려 옴
```

마지막 형태 때문에 **괄호 안의 숫자를 전부 뽑으면 안 된다.** "제2조 4호"의 2와 4까지
근거 번호로 읽혀, 인용하지도 않은 조항이 근거로 뜬다. 쉼표로 끊고 **조각마다 첫 숫자만**
취한다(조항 번호는 언제나 근거 번호 뒤에 온다). AI 서버도 같은 규칙으로 읽어
`cited`를 채운다 — 서버가 이미 판정해 보내므로, 프론트는 마커 "위치"만 찾고
유효성은 `index` 매칭 여부로 판단하면 된다.

주의할 두 가지:

- **`sources`에 없는 번호**를 모델이 쓰는 경우가 있다. 칩으로 만들지 말고 원문 텍스트
  그대로 두면 된다. 에러 토스트를 띄우면 모델이 번호 하나 잘못 쓸 때마다 사용자가 경고를 본다
- **마커가 아예 없는 답변**도 있다. 그때는 답변 하단에 "참고한 약관 조항 N건"처럼
  접어서 노출하는 폴백이 필요하다. 없으면 그런 답변에서는 근거를 볼 방법이 사라진다

**`coverages`가 이번 전환의 핵심이다.** 이걸 실어주면 두 가지가 해결된다.

1. **미가입 담보 오답 방지**
   약관에는 그 상품이 팔 수 있는 모든 특약이 실려 있다. 가입하지 않은 담보를 물어도
   조항이 검색되어 "보상됩니다"라는 틀린 답이 나간다. 실측 비교:

   > 질문: "항공기가 5시간 지연되면 보상받을 수 있나요?" (해당 특약 미가입)
   >
   > `coverages` 없음 — "…가입되어 있다면 보상받을 수 있습니다"
   > `coverages` 있음 — "약관상으로는 보상 대상에 해당하나, 가입 정보 확인 결과
   >                    미가입 상태이시므로 보상받으실 수 없습니다"

2. **실제 한도 금액 답변**
   약관은 "보험가입금액을 한도로"라고만 쓴다. 실측에서 약관 추출 30건 중 금액이
   채워진 것은 3건뿐이었고 LLM 모델 5종 전부 같았다. 모델 성능이 아니라 정보원의
   문제다. 그 값은 증권에만 있다.

3. **미가입 담보 판정** (`coveragesComplete`)
   `coverages`가 증권의 보장내용 표 **전체**이면 `true`로 보내주세요.
   그래야 목록에 없는 담보를 물었을 때 "가입하지 않으셨습니다"라고 답할 수 있다.

   > 질문: "골프용품이 파손되면 보상되나요?" (증권에 없는 담보)
   >
   > `false`(기본값) — "골프용품손해 특별약관에 **가입되어 있는 경우** 보상받을 수 있습니다"
   > `true`          — "**가입하지 않으셨으므로 보상받으실 수 없습니다**"
   >
   > 앞의 답을 사용자는 "보상된다"로 읽는다. 실제로는 보험금을 받지 못한다.

   일부만 보낼 때는 `false`로 두어야 한다. 파싱이 빠뜨린 담보를 미가입이라고
   답하면, 실제로는 보장되는데 안 된다고 오해시켜 더 나쁘다.

**`coverages`를 안 보내면 지금과 100% 동일하게 동작한다.** 기존 클라이언트는 수정 불필요.

`history`는 최근 3턴(6개) 정도면 충분하다. AI 서버는 `chat_messages`를 직접 조회하지
않는다. AI 계정에 전체 사용자 대화 열람 권한을 주지 않기 위해서다.

---

## 2. AI 서버가 보내는 콜백

### 2-1. 완료

```
POST {백엔드}/internal/analysis-results/{analysisResultId}/complete
```

```json
{
  "analysisResultId": "uuid",
  "status": "COMPLETED",
  "summary": "보장 항목 21개를 확인했습니다: ...",
  "coverageItems": [
    {
      "title": "해외의료비 보장 상해",
      "coverageStatus": "COVERED",
      "subtitle": null,
      "category": "해외의료비 보장",
      "limitLabel": "US 5만달러",
      "limitAmount": 50000,
      "limitCurrency": "USD",
      "conditions": "여행 중 상해/질병으로 해외에서 의료비 발생시 실제 부담한 의료비 보상",
      "detailItems": [], "subLimits": [], "requiredDocuments": [],
      "exclusions": [], "sources": []
    }
  ],
  "embeddingModel": "upstage-1536",
  "embeddingDimension": 1536,
  "rawResultJson": "...",

  "insurerName": "한화손해보험(주)",
  "productName": "해외여행자보험"
}
```

- 기존 필드는 그대로다. 스키마 변경 없음
- **추가되는 것은 `insurerName` / `productName` 둘뿐이다.** 증권 분석일 때만 채워진다
- Spring Boot는 기본값이 `fail-on-unknown-properties=false`라 그냥 무시된다.
  **혹시 이 설정을 켜두셨다면 알려주세요.** 켜져 있으면 400이 난다

`limitLabel`과 `limitAmount`를 둘 다 보내는 이유: `limitAmount`는 BIGINT라 정수만
들어가는데, 증권에는 "US 5만달러", "(정액) 50만원"처럼 통화와 지급 방식이 붙어 있다.
화면에는 원문(`limitLabel`)을 그대로 띄우는 편이 정확하다.

### 2-2. 실패

```
POST {백엔드}/internal/analysis-results/{analysisResultId}/fail
{ "analysisResultId": "uuid", "status": "FAILED", "errorMessage": "..." }
```

콜백은 실패 시 지수 백오프로 3회 재시도한다(2초 → 4초 → 8초).
**중복 수신이 발생할 수 있으니 멱등 처리 부탁드립니다.**

---

## 3. 백엔드가 맡아주셔야 하는 것

### 3-1. 증권 → 약관 연결 (중요)

증권 분석이 끝나면 콜백에 `insurerName` / `productName`이 실려 온다.
이걸로 **어느 약관인지 찾아 연결**해주셔야 챗봇이 근거를 댈 수 있다.

**단순 문자열 일치로는 안 된다.** 실제 데이터에서 확인한 것:

```
증권 "DB손해보험(주)" / "DB손보"      →  약관 "DB손해보험"          표기 흔들림
증권 "한화손해보험(주)"                →  약관 "캐롯 해외여행보험"     제휴 판매, 인수사가 다름
증권 "우리아이 여행보험"               →  "해외여행보험"으로 오매칭    (유사도 기준을 올려서 해결)
```

AI 서버에 참조 구현이 있다 (`app/services/terms_matcher.py`). 4단계로 내려간다.

| 단계 | 조건 | 화면 안내 |
|---|---|---|
| `EXACT` | 보험사 + 상품명(+개정판) 일치 | 없음 |
| `REVISION` | 같은 상품, 다른 개정판 | "가입 시점과 다른 개정판 약관을 참고했습니다" |
| `INSURER` | 같은 보험사, 다른 상품 | "동일 상품 약관이 없어 다른 상품 약관을 참고했습니다" |
| `NONE` | 그 보험사 약관 없음 | "약관을 보유하지 않아 증권 내용만으로 안내했습니다" |

**어떤 경우에도 실패로 처리하지 않는 것이 중요하다.** 보장 카드는 증권에서 나오므로
약관이 없어도 화면은 정상적으로 뜬다. 상세·챗봇만 기능이 줄어든다.

Java로 옮기기 부담스러우시면 AI 서버에 `/internal/terms/match` 엔드포인트로
열어드릴 수 있다. 편하신 쪽으로 알려주세요.

### 3-2. 공유 약관 테이블 (구조상 필요)

**약관은 사용자가 올리지 않는다.** 우리가 보험사별로 미리 분석해 저장해두고,
증권이 들어오면 거기 맞는 약관을 찾아 쓴다. 같은 상품의 약관은 사용자가 달라도
내용이 같으므로 한 번 색인해 모두가 공유한다.

그런데 `policy_chunks`에는 넣을 수가 없다.

```
user_id            UUID NOT NULL  → users(id)          미리 넣는 시점에 사용자가 없음
analysis_result_id UUID NOT NULL  → analysis_results   분석 요청이 없음
document_id        UUID NOT NULL  → policy_documents   업로드한 문서가 없음
```

셋 다 "누가 언제 올린 파일"을 가리키는데, 공유 약관에는 주인이 없다.

처음에는 이 테이블을 후순위로 봤다. 약관도 사용자 업로드로 들어온다고 보면
그 값들이 요청에 실려 오기 때문이다. 미리 배치로 넣는 구조로 정해지면서
**선택이 아니라 전제가 됐다.**

```sql
CREATE TABLE policy_terms (
    id            UUID PRIMARY KEY,
    insurer_name  VARCHAR(200) NOT NULL,
    product_name  VARCHAR(200) NOT NULL,
    revision      VARCHAR(50),
    source_file   VARCHAR(500),
    status        VARCHAR(20) NOT NULL,   -- PENDING / INDEXED / FAILED
    indexed_at    TIMESTAMP,
    created_at    TIMESTAMP NOT NULL,
    updated_at    TIMESTAMP NOT NULL,
    CONSTRAINT uk_policy_terms UNIQUE (insurer_name, product_name, revision)
);

CREATE TABLE policy_terms_chunks (
    id                  UUID PRIMARY KEY,
    terms_id            UUID NOT NULL,
    chunk_index         INTEGER NOT NULL,
    source_content_type VARCHAR(30) NOT NULL,
    page_start          INTEGER,
    page_end            INTEGER,
    section_title       VARCHAR(500),
    clause_path         VARCHAR(300),
    coverage_category   VARCHAR(100),
    clause_type         VARCHAR(30) NOT NULL,
    content             TEXT NOT NULL,
    embedding           vector(1536),
    char_count          INTEGER NOT NULL,
    created_at          TIMESTAMP NOT NULL,
    updated_at          TIMESTAMP NOT NULL,
    CONSTRAINT uk_policy_terms_chunks UNIQUE (terms_id, chunk_index),
    CONSTRAINT fk_policy_terms_chunks_terms FOREIGN KEY (terms_id) REFERENCES policy_terms(id)
);
CREATE INDEX idx_policy_terms_chunks_terms ON policy_terms_chunks(terms_id, clause_path);
```

`policy_chunks`에서 `user_id` / `trip_id` / `document_id` / `analysis_result_id`만
빠지고 나머지는 같다. `rag_service` 계정에 이 두 테이블 권한도 필요하다.

**당장 막히지는 않는다.** 그전까지는 AI 서버가 약관을 파일로 들고 검색한다
(`data/chunks` + `data/embeddings`, 배포 볼륨 `ai-data`). 컨테이너가 하나면
문제없이 돌고, 실제로 캐롯 약관 310청크로 검색 품질까지 확인했다(Recall@8 86.7%).

테이블이 생기면 달라지는 것:

| | 파일 (지금) | policy_terms |
|---|---|---|
| 동작 | O | O |
| 컨테이너 여러 개 | 각자 사본을 들고 있어야 함 | 공유 |
| 약관 목록 SQL 조회 | 불가 | 가능 |
| 3-1의 약관 연결 | 파일명 규칙에 의존 | `terms_id`로 명확 |

### 3-3. 챗봇 요청의 약관 지정 (합의 필요)

지금 `POST /internal/rag/query`는 `documentId`로 어느 약관을 볼지 정한다.
사용자가 약관을 올리던 시절의 필드다.

약관을 미리 넣는 구조에서는 **그 자리에 무엇을 보낼지 정해야 한다.**

- `policy_terms`가 생기면 → `termsId`를 보내주시면 된다. AI 쪽에 필드를 추가하겠다
- 그전까지는 → AI가 들고 있는 약관 식별자(예: `carrot_travel_2025`)를 그대로 보내면 된다

3-1에서 약관을 찾은 결과를 여기에 실어 보내는 흐름이다. **어느 쪽으로 할지
알려주시면 맞추겠다.** 지금은 후자로 동작한다.

---

## 4. 요약: 언제 무엇을 하면 되나

| 시점 | 백엔드가 할 일 | 안 하면 |
|---|---|---|
| **지금** | API 키 / Base URL / 파일 전달 방식 알려주기 | 연동 자체가 안 됨 |
| **지금** | `fail-on-unknown-properties` 설정 확인 | 콜백이 400 날 수 있음 |
| **지금** | 챗봇 요청에 약관을 어떻게 지정할지 합의 (3-3) | 챗봇이 어느 약관을 볼지 모름 |
| **지금** | 챗봇 응답 DTO에 `sources[]` 새 필드 추가 (1-2) | 근거 팝업을 못 만듦. `fail-on-unknown-properties`가 켜져 있으면 500 |
| **지금** | `sources`를 `chat_messages.metadata_json`에 저장 + 채팅 API로 전달 | 대화를 다시 열면 근거가 사라짐 |
| 여유 될 때 | 콜백의 `insurerName`/`productName`으로 약관 연결 (3-1) | 챗봇이 약관 근거를 못 댐 |
| 여유 될 때 | RAG 요청에 `coverages` / `coveragesComplete` 실어주기 | 미가입·한도 답변이 부정확 |
| 여유 될 때 | `documentType` 실어주기 | 페이지 수로 자동 판별 (대개 맞음) |
| 되는 대로 | `policy_terms` DDL (3-2) | 파일 저장소로 도는 중. 컨테이너 늘리면 막힘 |

앞의 3개를 뺀 나머지는 **안 해도 지금과 똑같이 동작한다.** 하나씩 채울 때마다
정확해질 뿐이다.

---

## 5. AI 서버 쪽 현황 (참고)

연동 전에 어디까지 확인됐는지 남긴다.

| | 상태 |
|---|---|
| 증권 PDF → 보장 카드 → 콜백 | 실제 증권으로 실행 확인. 담보 21건, 금액 21/21 |
| 챗봇 검색 → 답변 | 실제 약관(캐롯 310청크)으로 확인 |
| 콜백 경로·형식 | mock 서버로 수신 확인 |
| 약관 색인 | 8건 완료 (DB·현대2·KB·메리츠·삼성·캐롯) |
| 검색 품질 | Recall@8 88.0% (db_travel 25문항) / 86.7% (캐롯 15문항) |

개인정보는 분석이 끝나면 Upstage 서버와 로컬 디스크 양쪽에서 지운다.
증권에는 피보험자 이름·생년월일·증권번호가 들어 있어 남기지 않는다.
