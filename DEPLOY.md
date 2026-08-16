# 배포 안내

**Spring과 같은 EC2에 올리되, compose는 따로 둔다.**

```
[ EC2 ]
  ├─ polight-backend (Spring)      ─┐
  ├─ polight-postgres (pgvector)    ├─ docker network: polight_polight-network
  ├─ polight-ai      (우리)         ─┘
  └─ 우리만 compose 프로젝트가 다르다
```

DB는 RDS가 아니라 **같은 EC2의 `pgvector/pgvector:pg16` 컨테이너**다. 네트워크도
Spring compose가 이미 만들어 둔 것이 있어 **새로 만들 필요가 없다.** 우리는 거기
합류만 하므로 남의 컨테이너 설정을 건드리지 않는다.

## 왜 같은 호스트인가

처음에는 CPU 경합을 우려해 별도 EC2를 계획했으나, **실측해보니 근거가 없었다.**

```
분석 1건(182초) 동안의 우리 프로세스
  CPU     평균 0.1%  최대 6.5%  (14코어 기준)
  메모리   최대 189MB
```

182초 중 대부분이 Upstage·OpenAI **API 응답 대기**라 실제 연산이 거의 없다.
t3.medium(2 vCPU)으로 환산해도 평균 1% 미만이라 Spring이 느려질 일이 없다.

같은 호스트를 쓰면 **DB 컨테이너에 같은 도커 네트워크로 그대로 붙고**, 포트를
밖으로 열 필요도 없다. VPC·보안그룹을 맞추는 작업이 통째로 사라진다.

**단, 인스턴스 사양이 선행 조건이다.** 1GB 인스턴스에서는 여유 메모리가 200MB
남짓이라, 우리 컨테이너를 올리면 OOM 킬러가 가장 큰 프로세스인 **JVM(Spring)을
먼저 죽인다.** t3.medium(4GB) 이상에서만 올린다. 디스크도 8GB로는 빠듯해
20GB를 권한다. 디스크가 차도 Spring이 같이 죽는다.

## 왜 compose는 따로인가

같은 compose에 넣으면 **Spring을 배포할 때마다 우리 컨테이너도 재시작된다.**

분석은 3~4분간 프로세스 안에서 돈다(`BackgroundTasks`). 그 사이 재시작되면
작업이 통째로 날아가고, 콜백을 못 보내 `analysis_results`가 `PROCESSING`에
고착된다. **에러도 로그도 남지 않아 알아채기 어렵다.**

`docker network`만 공유하면 컨테이너끼리 이름으로 통신하면서 배포는 독립적이다.

## 백엔드에서 받아야 하는 것

| 항목 | 왜 필요한가 |
| --- | --- |
| **콜백 경로** | 우리가 정한 경로로 만들어 뒀다. 다르면 알려주면 환경변수로 맞춘다 |
| `INTERNAL_API_KEY` | **아직 Spring 쪽에 없다.** 새로 만들어 양쪽이 같은 값을 쓴다. 문서·커밋에 넣지 않고 별도 채널로 주고받는다 |
| DB 비밀번호 | `polight-postgres` 컨테이너 환경변수에 있다 |
| 인스턴스 상향 | t3.medium + EBS 20GB. 중지가 필요하니 트래픽 없는 시간에 |

**VPC ID도 RDS 보안그룹도 필요 없다.** DB가 같은 호스트의 컨테이너이기 때문이다.
`SPRING_BASE_URL`도 물어볼 필요 없이 `http://polight-backend:8080`으로 고정이다.

## 환경변수

`.env` 또는 컨테이너 환경변수로 넣는다.

```bash
# 필수
OPENAI_API_KEY=...            # 답변 생성, 보장항목 추출
UPSTAGE_API_KEY=...           # 문서 파싱, 임베딩
DATABASE_URL=postgresql://polight:<비밀번호>@postgres:5432/polight
SPRING_BASE_URL=http://polight-backend:8080
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

**사전 준비는 없다.** Spring compose가 만든 `polight_polight-network`에 합류하므로
네트워크를 만들 필요도, 백엔드 compose를 고칠 필요도 없다.

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

백엔드가 `docker compose down`을 하면 우리 컨테이너가 붙어 있어 네트워크 삭제에
실패했다는 경고가 뜬다. 무해하다. 네트워크는 남고 양쪽 다 정상 동작한다.

Spring을 배포해도 이 컨테이너는 건드려지지 않는다. 반대도 마찬가지다.

## 서로 부르는 주소

컨테이너 이름으로 통신한다. IP를 알 필요가 없다.

```
Spring -> AI      http://polight-ai:8000/internal/rag/query
AI -> Spring      SPRING_BASE_URL=http://polight-backend:8080
AI -> DB          postgresql://polight:...@postgres:5432/polight
```

DB 호스트가 `polight-postgres`가 아니라 `postgres`인 것은 compose 서비스명 별칭이라
그렇다. Spring도 같은 주소를 쓰고 있다.

## 포트를 열지 않는다

`ports`가 아니라 `expose`를 쓴다. 같은 네트워크의 컨테이너만 접근하면 되므로
호스트에도, 외부에도 노출할 이유가 없다. **보안그룹으로 막는 것보다 아예 열지
않는 편이 확실하다.**

검증한 내용이다.

```
호스트에서 localhost:8000        연결 안 됨
같은 네트워크의 다른 컨테이너      http://polight-ai:8000/health -> 200
키 없이 /internal                401
맞는 키                          200
```

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
