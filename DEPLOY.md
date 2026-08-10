# 배포 안내

AI 서버는 Spring과 **분리된 별도 EC2**에 올린다. 같은 서버에 얹으면 약관 분석
(파싱·임베딩·추출)이 도는 동안 CPU 경합으로 Spring API 응답까지 느려진다.

```
[Spring EC2] ──(8000)──> [AI EC2] ──(5432)──> [RDS]
      ^                      │
      └────(콜백)────────────┘
```

## 백엔드에서 받아야 하는 것

| 항목 | 왜 필요한가 |
| --- | --- |
| **VPC ID / 서브넷** | RDS와 같은 VPC에 EC2를 만들어야 DB에 붙는다. 다른 VPC에 만들면 인스턴스를 다시 만들어야 하므로 **생성 전에** 받아야 한다 |
| RDS 보안그룹 | AI EC2에서 오는 5432 인바운드 허용 |
| `rag_service` 계정 + RDS 엔드포인트 | `DATABASE_URL`에 넣는다 |
| Spring 보안그룹 | AI EC2 → Spring (콜백용) |
| `INTERNAL_API_KEY` | Spring과 같은 값. 문서·커밋에 넣지 않고 별도 채널로 받는다 |

## 환경변수

`.env` 또는 컨테이너 환경변수로 넣는다.

```bash
# 필수
OPENAI_API_KEY=...            # 답변 생성, 보장항목 추출
UPSTAGE_API_KEY=...           # 문서 파싱, 임베딩
DATABASE_URL=postgresql://rag_service:...@<rds-endpoint>:5432/polight
SPRING_BASE_URL=http://<spring-host>:8080
INTERNAL_API_KEY=...          # openssl rand -base64 32, Spring과 동일

# 선택 (기본값 있음)
ANSWER_PROVIDER=openai-41     # 답변 모델
EXTRACTION_PROVIDER=openai-41 # 추출 모델. 실서비스 전 claude-opus로 올릴 예정
EMBEDDING_PROVIDER=upstage-1536
DB_POOL_MAX=10                # 동시 대화 5건 기준
LOG_LEVEL=INFO
```

`SPRING_BASE_URL`이 설정된 상태에서 `INTERNAL_API_KEY`가 없으면 **기동이 실패한다.**
설정을 잊었는데 조용히 열려 있는 상태가 가장 위험하기 때문에 일부러 그렇게 했다.

## 실행

```bash
docker build -t polight-ai .
docker run -d --name polight-ai -p 8000:8000 --env-file .env --restart unless-stopped polight-ai
curl http://localhost:8000/health
```

## 보안그룹

| 방향 | 포트 | 출처/대상 |
| --- | --- | --- |
| 인바운드 | 8000 | **Spring EC2에서만.** 외부 공개하지 않는다 |
| 아웃바운드 | 5432 | RDS |
| 아웃바운드 | 8080 | Spring (콜백) |
| 아웃바운드 | 443 | OpenAI / Upstage API |

보안그룹으로 8000을 좁히는 것과 `X-Internal-Api-Key` 검증이 **2중 방어**를 만든다.
어느 한쪽만으로도 막히지만, 보안그룹은 잘못 열기 쉽고 키는 유출될 수 있다.

## 엔드포인트

| 경로 | 인증 | 용도 |
| --- | --- | --- |
| `GET /health` | 없음 | 로드밸런서·EC2 상태 확인 |
| `POST /internal/analysis` | 필요 | 약관 분석 요청 (202 즉시 응답, 비동기 처리) |
| `POST /internal/rag/query` | 필요 | 챗봇 질의 |

`/health`만 열려 있다. 헬스체크가 키를 실어 보내지 않기 때문이다.

## 알아둘 것

**분석이 약관 1건에 4~5분 걸린다.** 파싱·임베딩·추출을 모두 하기 때문이다.
비동기라 사용자가 기다리지는 않지만, **Spring 쪽 콜백 타임아웃을 넉넉히** 잡아야 한다.

**분석은 응답을 보낸 뒤에도 계속 돈다**(BackgroundTasks). 요청이 끝나면 컨테이너를
정리하는 서버리스 환경에 그대로 올리면 안 된다. EC2 상주 프로세스를 전제로 한다.

**이미지가 1GB 정도다.** pymupdf와 numpy 계열이 대부분이다. 빌드 시간이 아깝다면
`requirements.txt`가 바뀌지 않는 한 레이어 캐시가 재사용된다.

**EC2 권한 승인이 늦어지면** `ngrok`으로 터널을 열어 배포 없이 연동 테스트를
먼저 할 수 있다. 단 **DB 접속은 터널로 해결되지 않으므로** 그때까지는 로컬
pgvector(`docker compose up -d`)로 개발한다.

## 검증된 것

로컬에서 이미지를 빌드해 확인한 내용이다.

```
빌드          성공 (1.03GB)
기동          2초
GET /health   200
인증          키 없이 401 / 맞는 키 통과
실제 질의     200, 8.0초, 출처 8개
```
