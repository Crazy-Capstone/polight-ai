# Polight 기술 아키텍처 다이어그램

3개 레포(`Polight-frontend` · `Polight-Server` · `polight-ai`)의 실제 설정 파일
(`pubspec.yaml` · `vercel.json` · `build.gradle` · `application.yaml` ·
`docker-compose.prod.yml` · `requirements.txt`)에서 확인한 스택으로 작성했다.

## 무엇을 쓰면 되는가

| 용도 | 파일 |
| --- | --- |
| **발표·문서용 (권장)** | `polight-architecture.drawio` — 실제 기술 로고 + AWS 아이콘. draw.io 로 열어 편집·내보내기 |
| GitHub 에서 바로 보기 | 이 문서 아래의 Mermaid 블록 |
| 텍스트로 diff 를 보고 싶을 때 | `overview.mmd` · `ai-pipeline.mmd` |

| 파일 | 내용 |
| --- | --- |
| `polight-architecture.drawio` | 탭 2개 — **전체 아키텍처** / **AI 파이프라인**. 로고는 파일에 임베드되어 있어 오프라인에서도 그대로 보인다 |
| `overview.drawio.png` · `ai-pipeline.drawio.png` | 위 파일의 **배치 미리보기**. 좌표·라벨 확인용이며, 선은 직선으로만 그렸다. 최종 이미지는 draw.io 에서 내보낸다 |
| `overview.mmd` · `ai-pipeline.mmd` | 같은 내용의 Mermaid 소스 |
| `overview.png` · `ai-pipeline.png` | Mermaid 를 렌더링한 이미지 |
| `tools/` | 두 다이어그램을 생성하는 스크립트 (좌표·스타일이 코드로 선언돼 있어 수정·재생성이 쉽다) |

## draw.io 로 여는 방법

1. [app.diagrams.net](https://app.diagrams.net) → **File → Open from → Device** → `polight-architecture.drawio`
2. 아래쪽 탭으로 **전체 아키텍처 / AI 파이프라인** 전환
3. 내보내기: **File → Export as → PNG** (배율 2~3배, *Transparent* 끄기)

### 편집할 때 알아둘 것

- **그룹이 실제로 중첩돼 있다.** `AWS Cloud → Region → VPC → Public subnet → EC2` 순서로
  부모-자식이라, VPC 를 옮기면 안에 든 컨테이너가 같이 움직인다.
- **AWS 아이콘은 draw.io 내장 스텐실**(`mxgraph.aws4.*`)이다. 다른 서비스로 바꾸려면
  도형을 우클릭 → *Edit Style* 에서 `resIcon=mxgraph.aws4.<서비스>` 만 고치면 된다.
- **기술 로고는 SVG 가 파일에 임베드**되어 있다(퍼센트 인코딩 data URI). 외부 링크가 아니라
  네트워크 없이도 보이고, 로고를 바꾸려면 이미지를 교체하면 된다.
- 다시 생성하려면: `cd tools && node icons.js && python3 buildall.py`

## 로고 포함 다이어그램

### 전체 아키텍처

![전체 아키텍처 배치 미리보기](./overview.drawio.png)

### AI 파이프라인

![AI 파이프라인 배치 미리보기](./ai-pipeline.drawio.png)

> 위 두 이미지는 배치 확인용 미리보기다. AWS 아이콘과 곡선 라우팅이 들어간 최종 그림은
> `polight-architecture.drawio` 를 draw.io 에서 열어 내보낸다.

## 스택 요약

| 레이어 | 기술 |
| --- | --- |
| 클라이언트 | Flutter 3.9 · Dart (Web / iOS / Android 단일 코드베이스), `flutter_secure_storage`, `webview_flutter`, `geolocator`, `file_picker` |
| 웹 배포 | Vercel — `flutter build web --release` + `vercel.json` 의 `/api/*` rewrite |
| 백엔드 | Spring Boot 3.5.6 · Java 17 · Spring Security + JWT(jjwt) · Spring Data JPA · Flyway · springdoc-openapi · AWS SDK for S3 |
| AI 서버 | FastAPI · Uvicorn · Python 3.14 · Pydantic · PyMuPDF · LangChain |
| 데이터 | PostgreSQL 16 + pgvector (`pgvector/pgvector:pg16` 컨테이너) |
| 파일 저장 | S3 (증권·약관 PDF, Presigned GET 15분) |
| 인증 | Kakao OAuth 2.0 Authorization Code → 서비스 JWT (만료 1시간) |
| 외부 API | Upstage(Document Parse · Studio Agent · Embedding), OpenAI GPT-4.1, Google Gemini, Anthropic Claude, Google Places API |
| 인프라 | AWS EC2(단일 인스턴스) · Docker Compose · `polight-network` 로 컨테이너 간 통신 |
| CI/CD | 백엔드: GitHub Actions → OIDC → ECR Push → SSM RunShellScript → `docker compose up -d` / AI: 호스트에서 이미지 빌드 후 별도 compose / 프론트: `deploy.sh` → `vercel --prod` |

## 서비스 간 통신

| 구간 | 내용 |
| --- | --- |
| 클라이언트 → 백엔드 | 웹은 Vercel `/api/*` rewrite 경유, 모바일 빌드는 직접 호출. `Authorization: Bearer {JWT}` |
| 백엔드 → AI | `POST /internal/analysis` (202 즉시 응답) · `POST /internal/rag/query` (read timeout 60s). 공유 시크릿 `INTERNAL_API_KEY` |
| AI → 백엔드 | `PUT /internal/analysis-results/{id}` 분석 완료/실패 콜백 |
| AI → S3 | 백엔드가 발급한 Presigned GET URL 로 PDF 수신 |
| 컨테이너 간 | Docker 네트워크 이름으로 통신(`polight-backend:8080`, `polight-ai:8000`, `postgres:5432`). AI 서버는 호스트 포트를 열지 않는다 |

## 배포를 두 compose 로 나눈 이유

AI 서버의 분석 작업은 응답을 보낸 뒤에도 `BackgroundTasks` 로 3~4분간 계속 돈다.
같은 compose 에 두면 백엔드를 배포할 때마다 AI 컨테이너가 재시작되어 진행 중인 분석이
사라지고 콜백을 못 보내 `analysis_results` 가 `PROCESSING` 에 고착된다.
그래서 네트워크만 공유하고 compose 프로젝트는 분리했다.

---

## Mermaid 버전

draw.io 없이 GitHub 에서 바로 보거나, 텍스트로 관리하고 싶을 때 쓴다.

## 1. 전체 시스템 아키텍처

![전체 아키텍처](./overview.png)

```mermaid
flowchart LR
    %% ===== 클라이언트 =====
    subgraph CLIENT["Client"]
        direction TB
        APP["<b>Polight App</b><br/>Flutter 3.9 · Dart<br/>Web / iOS / Android 단일 코드베이스<br/>flutter_secure_storage · webview_flutter<br/>geolocator · file_picker"]
        VERCEL["<b>Vercel</b> — Web 배포<br/>flutter build web --release<br/>vercel.json rewrite : /api/* → API 서버"]
    end

    %% ===== 클라이언트가 직접 부르는 외부 API =====
    subgraph EXT["External API"]
        direction TB
        KAKAO["<b>Kakao Login</b><br/>OAuth 2.0<br/>Authorization Code"]
        PLACES["<b>Google Places API</b><br/>searchNearby · searchText"]
    end

    %% ===== EC2 (컨테이너) =====
    subgraph EC2["AWS EC2 · Docker Compose (polight-network)"]
        direction TB
        BE["<b>polight-backend</b><br/>Spring Boot 3.5.6 · Java 17<br/>Spring Security + JWT<br/>Spring Data JPA · Flyway<br/>springdoc-openapi"]
        AI["<b>polight-ai</b><br/>FastAPI · Uvicorn · Python 3.14<br/>Pydantic · PyMuPDF<br/>내부 전용 (expose 8000)"]
        DB[("<b>polight-postgres</b><br/>PostgreSQL 16 + pgvector")]
    end

    %% ===== AWS 관리형 서비스 =====
    subgraph MANAGED["AWS Managed Service · ap-northeast-2"]
        direction TB
        S3["<b>S3</b><br/>증권 · 약관 PDF<br/>Presigned GET (15분)"]
        ECR["<b>ECR</b><br/>polight/backend 이미지"]
        SSM["<b>Systems Manager</b><br/>배포 명령 실행"]
    end

    %% ===== AI 벤더 =====
    subgraph VENDOR["AI Provider"]
        direction TB
        UPSTAGE["<b>Upstage</b><br/>Document Parse · Studio Agent<br/>Embedding (upstage-1536)"]
        LLM["<b>LLM</b> — .env 로 교체<br/>OpenAI GPT-4.1<br/>Google Gemini · Anthropic Claude"]
    end

    %% ===== CI/CD =====
    subgraph CICD["CI / CD"]
        direction TB
        GHA["<b>GitHub Actions</b><br/>OIDC 인증 → 이미지 빌드"]
        FEDEP["<b>deploy.sh</b><br/>flutter build web → vercel --prod"]
    end

    %% ===== 클라이언트 → 서버 =====
    APP -->|"HTTPS"| VERCEL
    APP -.->|"인가 코드 발급 (WebView)"| KAKAO
    APP -.->|"주변 병원 · 대사관 검색"| PLACES
    VERCEL -->|"/api/* reverse proxy"| BE
    APP -.->|"모바일 빌드는 직접 호출<br/>Bearer JWT · REST"| BE

    %% ===== 백엔드 =====
    BE -->|"인가 코드 → 사용자 프로필"| KAKAO
    BE -->|"JDBC"| DB
    BE -->|"PDF 업로드<br/>Presigned URL 발급"| S3
    BE -->|"POST /internal/analysis (202)<br/>POST /internal/rag/query<br/>X-Internal-Api-Key"| AI

    %% ===== AI 서버 =====
    AI -->|"분석 완료 콜백<br/>PUT /internal/analysis-results/{id}"| BE
    AI -->|"Presigned GET 으로 PDF 수신"| S3
    AI -->|"약관 청크 벡터 검색"| DB
    AI -->|"파싱 · 임베딩 · 담보 추출"| UPSTAGE
    AI -->|"답변 생성 · 보장 규칙 추출"| LLM

    %% ===== 배포 =====
    GHA -->|"push"| ECR
    ECR -->|"pull"| SSM
    SSM -->|"docker compose up -d"| BE
    FEDEP --> VERCEL

    %% ===== 스타일 (draw.io 에서 오류 나면 이 아래만 삭제) =====
    classDef client fill:#E3F2FD,stroke:#1565C0,color:#0D47A1
    classDef infra fill:#FFF3E0,stroke:#EF6C00,color:#E65100
    classDef ai fill:#F3E5F5,stroke:#7B1FA2,color:#4A148C
    classDef data fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef ext fill:#ECEFF1,stroke:#546E7A,color:#263238
    class APP,VERCEL client
    class BE,ECR,SSM,S3 infra
    class AI,UPSTAGE,LLM ai
    class DB data
    class KAKAO,PLACES,GHA,FEDEP ext
```

## 2. AI 파이프라인

![AI 파이프라인](./ai-pipeline.png)

```mermaid
flowchart TB
    %% ===== 3. 챗봇 RAG (실시간) =====
    subgraph RAG["3. AI 챗봇 — 실시간 RAG (read timeout 60s)"]
        direction TB
        R1["Spring → AI<br/>POST /internal/rag/query<br/>질문 · tripId · termsId · 최근 대화 6개(3턴)"]
        R2["query_rewriter<br/>검색용 질문 재작성 (LLM)"]
        R3["Upstage Embedding<br/>upstage-1536"]
        R4["<b>하이브리드 검색</b><br/>pgvector 코사인 + BM25"]
        R5["MMR reranker<br/>중복 제거 · 근거 다양성 확보"]
        R6["prompt_builder<br/>근거 청크 + 대화 이력 프롬프트"]
        R7["answer_providers<br/>OpenAI GPT-4.1 / Gemini / Claude<br/>(.env 한 줄로 벤더 교체)"]
        R8["답변 + 약관 근거 반환<br/>근거 0건이면 정해진 문구로 응답"]
        R1 --> R2 --> R3 --> R4 --> R5 --> R6 --> R7 --> R8
    end

    %% ===== 2. 약관 색인 (운영자 사전 작업) =====
    subgraph INGEST["2. 약관 색인 — 운영자 사전 작업 (오프라인 · 1건 4~5분)"]
        direction TB
        I1["약관 PDF<br/>보험사 공시자료"]
        I2["ingest_terms.py<br/>Upstage Document Parse → 청킹 → 임베딩"]
        I3["migrate_terms_to_db.py<br/>청크 → policy_terms_chunks"]
        I4["migrate_terms_coverages.py<br/>coverage_extractor (LLM)<br/>면책 · 세부한도 · 청구서류 → policy_terms_coverages"]
        I1 --> I2
        I2 --> I3
        I2 --> I4
    end

    %% ===== 1. 증권 분석 (사용자 업로드 · 비동기) =====
    subgraph CERT["1. 증권 분석 — 사용자 업로드 (비동기 · 수십 초)"]
        direction TB
        C1["Spring → AI<br/>POST /internal/analysis<br/>analysisResultId · userId · tripId<br/>documentId · downloadUrl · documentType"]
        C2["<b>202 Accepted</b> 즉시 응답<br/>이후 BackgroundTasks 로 계속 처리"]
        C3["S3 Presigned GET<br/>증권 PDF 다운로드"]
        C4["<b>Upstage Studio Agent</b><br/>파싱 → 분류 → 추출<br/>담보 · 한도 · 보험기간"]
        C5["certificate_adapter<br/>담보 payload 변환"]
        C6["category_mapping<br/>표준 보장 카테고리 8종 환산"]
        C7["terms_matcher<br/>증권 ↔ 보유 약관 매칭"]
        C8["Spring 콜백<br/>PUT /internal/analysis-results/{id}<br/>담보 목록 · 보험사 · 상품명 · 보험기간"]
        C9["증권 PDF 삭제<br/>(피보험자 정보 · 증권번호 보호)"]
        C1 --> C2 --> C3 --> C4 --> C5
        C5 --> C6
        C5 --> C7
        C6 --> C8
        C7 --> C8
        C8 --> C9
    end

    %% ===== 저장소 =====
    DB[("<b>PostgreSQL 16 + pgvector</b><br/>policy_terms_chunks<br/>policy_terms_coverages")]
    CAT["<b>표준 보장 카테고리 8종</b><br/>의료비 · 항공지연 · 수하물 · 긴급이송<br/>치과응급 · 배상책임 · 여행취소 · 사망후유장해"]

    I3 --> DB
    I4 --> DB
    DB --> R4
    C6 --- CAT
    CAT --- C7

    %% ===== 스타일 (draw.io 에서 오류 나면 이 아래만 삭제) =====
    classDef entry fill:#E3F2FD,stroke:#1565C0,color:#0D47A1
    classDef proc fill:#F3E5F5,stroke:#7B1FA2,color:#4A148C
    classDef vendor fill:#FFF3E0,stroke:#EF6C00,color:#E65100
    classDef store fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    class C1,C2,C8,C9,R1,R8 entry
    class C5,C6,C7,I2,I3,I4,R2,R5,R6 proc
    class C3,C4,R3,R7 vendor
    class DB,CAT,R4,I1 store
```

세 흐름의 성격이 다르다.

| 흐름 | 트리거 | 특징 |
| --- | --- | --- |
| 증권 분석 | 사용자 업로드 | 비동기(202 + 콜백). 수십 초. DB 미사용. 담보 추출은 Upstage Studio Agent 가 담당 |
| 약관 색인 | 운영자 사전 작업 | 오프라인 스크립트. 1건 4~5분. 결과를 pgvector 에 적재 |
| 챗봇 RAG | 사용자 질문 | 동기 응답. 하이브리드 검색(벡터+BM25) → MMR → LLM |

증권의 담보와 약관의 보장 규칙은 **표준 보장 카테고리 8종**을 공통 키로 연결된다.
