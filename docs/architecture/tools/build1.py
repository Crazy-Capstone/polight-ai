# -*- coding: utf-8 -*-
from gen import *

d = Diagram('전체 아키텍처', 1740, 1065)

d.node('title', 'text;html=1;align=left;verticalAlign=middle;fontSize=20;fontStyle=1;fontColor=#1A365D;',
       40, 8, 600, 32, 'Polight — 기술 아키텍처')
d.node('subtitle', 'text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#4A5568;',
       42, 34, 700, 18, '여행자 보험 증권 · 약관 AI 분석 서비스 · AZAMS')

# ── 클라이언트 ────────────────────────────────────────────────
d.node('g_client', st_plain_group('#1565C0', '#F5FAFF', '#1565C0'), 40, 60, 560, 230, 'Client')
d.node('user', st_aws_general('user'), 70, 120, 62, 62, 'User', 'g_client')
d.node('app', st_logo('flutter'), 215, 115, 52, 52,
       '<b>Polight App</b><br>Flutter 3.9 · Dart<br>Web / iOS / Android', 'g_client')
d.node('vercel', st_logo('vercel'), 440, 115, 52, 52,
       '<b>Vercel</b><br>정적 웹 호스팅<br>/api/* rewrite', 'g_client')

# ── CI / CD ──────────────────────────────────────────────────
d.node('g_cicd', st_plain_group('#546E7A', '#FAFAFA', '#37474F'), 40, 335, 250, 205, 'CI / CD')
d.node('gh', st_logo('github'), 75, 390, 48, 48, '<b>GitHub Actions</b><br>OIDC', 'g_cicd')
d.node('fedep', st_box('#546E7A', '#FFFFFF', '#37474F'), 60, 478, 210, 44,
       'deploy.sh<br>flutter build web → vercel --prod', 'g_cicd')

# ── 외부 API ─────────────────────────────────────────────────
d.node('g_ext', st_plain_group('#546E7A', '#FAFAFA', '#37474F'), 1420, 60, 290, 250, 'External API')
d.node('kakao', st_logo('kakao'), 1520, 110, 48, 48,
       '<b>Kakao Login</b><br>OAuth 2.0 인가 코드', 'g_ext')
d.node('gmaps', st_logo('gmaps'), 1520, 215, 48, 48,
       '<b>Google Places API</b><br>searchNearby', 'g_ext')

# ── AI 벤더 ──────────────────────────────────────────────────
d.node('g_vendor', st_plain_group('#7B1FA2', '#FCF7FF', '#7B1FA2'), 1420, 350, 290, 555, 'AI Provider')
d.node('upstage', st_box('#7B1FA2', '#FFFFFF', '#4A148C'), 1450, 395, 230, 76,
       '<b>Upstage</b><br>Document Parse · Studio Agent<br>Embedding (upstage-1536)', 'g_vendor')
d.node('openai', st_box('#7B1FA2', '#FFFFFF', '#4A148C'), 1450, 500, 230, 56,
       '<b>OpenAI</b><br>GPT-4.1', 'g_vendor')
d.node('gemini', st_logo('gemini'), 1520, 592, 48, 48, '<b>Google Gemini</b>', 'g_vendor')
d.node('claude', st_logo('claude'), 1520, 692, 48, 48, '<b>Anthropic Claude</b>', 'g_vendor')
d.node('vnote', st_box('#B39DDB', '#F3E5F5', '#4A148C'), 1450, 790, 230, 44,
       'ANSWER_PROVIDER 환경변수로<br>코드 변경 없이 벤더 교체', 'g_vendor')

# ── AWS ──────────────────────────────────────────────────────
d.node('cloud', st_group('cloud'), 340, 340, 1030, 695, 'AWS Cloud')
d.node('region', st_group('region'), 375, 385, 960, 625, 'ap-northeast-2', 'cloud')
d.node('vpc', st_group('vpc'), 410, 430, 640, 540, 'VPC', 'region')
d.node('subnet', st_group('public'), 442, 475, 580, 470, 'Public subnet', 'vpc')
d.node('ec2g', st_group('ec2'), 474, 520, 520, 400, 'EC2 · t3.medium · Docker Compose', 'subnet')

d.node('docker', st_logo('docker'), 930, 532, 36, 36, '', 'ec2g')
d.node('be', st_logo('spring'), 520, 585, 52, 52,
       '<b>polight-backend</b> :8080<br>Spring Boot 3.5.6 · Java 17<br>Security+JWT · JPA · Flyway', 'ec2g')
d.node('db', st_logo('postgresql'), 850, 660, 52, 52,
       '<b>polight-postgres</b><br>PostgreSQL 16 + pgvector', 'ec2g')
d.node('ai', st_logo('fastapi'), 520, 790, 52, 52,
       '<b>polight-ai</b> :8000<br>FastAPI · Python 3.14<br>Pydantic · PyMuPDF', 'ec2g')

d.node('s3', st_aws_res('s3'), 1150, 460, 56, 56,
       '<b>S3</b><br>증권 · 약관 PDF<br>Presigned GET 15분', 'region')
d.node('ecr', st_aws_res('ecr'), 1150, 640, 56, 56,
       '<b>ECR</b><br>polight/backend 이미지', 'region')
d.node('ssm', st_aws_res('systems_manager'), 1150, 820, 56, 56,
       '<b>Systems Manager</b><br>배포 명령 실행', 'region')

# ── 연결 ─────────────────────────────────────────────────────
d.edge('user', 'app', '앱 접속')
d.edge('app', 'vercel', 'HTTPS')
d.edge('vercel', 'be', '/api/* reverse proxy')
d.edge('app', 'kakao', '인가 코드 발급 (WebView)', EDGE_DASH)
d.edge('app', 'gmaps', '주변 병원 · 대사관 검색', EDGE_DASH)
d.edge('be', 'kakao', '인가 코드 → 사용자 프로필', EDGE, exit=('1', '0.25'), entry=('0', '0.5'))
d.edge('be', 'db', 'JDBC')
d.edge('be', 's3', 'PDF 업로드 · Presigned URL 발급', EDGE, exit=('1', '0'), entry=('0', '0.5'))
d.edge('be', 'ai', 'POST /internal/analysis (202)\nPOST /internal/rag/query\nX-Internal-Api-Key',
       EDGE, exit=('0.25', '1'), entry=('0.25', '0'), lpos='-0.45')
d.edge('ai', 'be', 'PUT /internal/analysis-results/{id}\n분석 완료 콜백',
       EDGE, exit=('0.75', '0'), entry=('0.75', '1'), lpos='0.45')
d.edge('ai', 's3', 'Presigned GET 으로 PDF 수신', EDGE, exit=('1', '0.25'), entry=('0', '1'))
d.edge('ai', 'db', '약관 청크 벡터 검색')
d.edge('ai', 'upstage', '파싱 · 임베딩 · 담보 추출', EDGE, exit=('1', '0.5'), entry=('0', '0.5'))
d.edge('ai', 'openai', '답변 생성 · 보장 규칙 추출', EDGE, exit=('1', '0.75'), entry=('0', '0.5'))
d.edge('gh', 'ecr', '이미지 push', EDGE, exit=('1', '0.5'), entry=('0', '0.5'), lpos='-0.55')
d.edge('ecr', 'ssm', 'pull')
d.edge('ssm', 'be', 'docker compose up -d', EDGE, exit=('0', '0.5'), entry=('1', '0.75'), lpos='-0.5')
d.edge('fedep', 'vercel', '배포')

if __name__ == '__main__':
    open('preview1.svg', 'w').write(d.preview())
    print('ok')
