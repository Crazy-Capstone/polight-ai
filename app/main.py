from fastapi import FastAPI

from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)


# 스켈레톤이 실제로 기동되는지 확인용. 정식 라우터는 3단계(api/routes)에서 구성.
@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
