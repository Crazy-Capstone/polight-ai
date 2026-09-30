from datetime import date
from typing import Literal

from app.schemas.base import CamelModel


# 증권 분석 콜백에 실려 오는 값 그대로 보내면 된다.
class TermsMatchRequest(CamelModel):
    insurer_name: str
    product_name: str | None = None

    # 보험 시작일. 개정판이 여럿일 때 어느 판으로 가입했는지를 이 값으로 가른다.
    #
    # 없으면 최신판을 준다. 증권에서 기간을 못 읽는 경우가 있어 필수로 두지 않았다.
    insurance_start_date: date | None = None


class TermsMatchResponse(CamelModel):
    # 적재된 policy_terms.id. 이 값을 analysis_results.matched_terms_id에 넣으면 된다.
    # 못 찾았으면 null이다.
    terms_id: str | None = None

    # EXACT   보험사·상품·가입 시점 판까지 맞음
    # REVISION 같은 상품인데 가입 시점 판이 없어 다른 판을 쓴다
    # INSURER  같은 보험사의 다른 상품뿐이다
    # NONE     그 보험사 약관이 없다
    level: Literal["EXACT", "REVISION", "INSURER", "NONE"]

    # 사용자에게 보여줄 안내. EXACT면 null이다.
    # 근거의 신뢰도를 사용자가 알아야 해서 문구까지 같이 준다.
    notice: str | None = None

    # 어느 약관을 골랐는지. 로그와 화면 확인용이다.
    insurer_name: str | None = None
    product_name: str | None = None
    revision: str | None = None
