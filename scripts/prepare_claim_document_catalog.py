"""Build the reviewed research catalog from the supplied text export.

This prepares data only; it does not change the chatbot response contract.
Usage: python scripts/prepare_claim_document_catalog.py <research.txt>
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'config' / 'claim_documents'
DATE = '2026-11-21'
NOTION = 'https://checkered-trick-560.notion.site/41c965fbd35d80d8b1a9f65dab61690a'
URLS = {
    'samsung_property': 'https://www.samsungfire.com/v2/html/claim/08/C_080_100_001.html?tab=1',
    'samsung_medical': 'https://www.samsungfire.com/v2/html/customer/13/G_030_010_010.html',
    'kb': 'https://www.kbinsure.co.kr/CG205020007.ec',
    'meritz': 'https://www.meritzfire.com/compensation/longterm-insurance/request-document.do#!/',
    'hyundai': 'https://www.hi.co.kr/serviceAction.do?menuId=100636',
    'db_travel': 'https://www.idbins.com/pc/bizxpress/ct/dc/FWCUSV1302.shtm',
    'db_general': 'https://www.idbins.com/pc/bizxpress/ct/dc/FWCUSV1301.shtm',
    'hanwha': 'https://www.hwgeneralins.com/fplaza/compensation/receipt05.do',
    'carrot': 'https://www.carrotins.com/desktop/reward/claim/guide/?afcDtcd=direct_pc_gnb&afccd=PA01096&main=y&page=GuideDocCheck',
}


def clean(text):
    return text.replace('**', '').strip()


def tables(text):
    """Read multiline Markdown rows without flattening their condition text."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith('|') and i + 1 < len(lines) and re.match(r'^\s*\|[\s:|\-]+$', lines[i + 1]):
            header = [clean(c) for c in lines[i].strip().strip('|').split('|')]
            start = i
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                buf = lines[i].strip()
                i += 1
                while not buf.rstrip().endswith('|') and i < len(lines):
                    buf += '\n' + lines[i].strip()
                    i += 1
                cells = [clean(c) for c in buf.strip().strip('|').split('|')]
                if len(cells) != len(header):
                    raise ValueError(f'Invalid row at relative line {i}: {cells}')
                rows.append(cells)
            yield start, header, rows
        else:
            i += 1


def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def profile(pid, name, url_key, verification):
    return {'id': pid, 'title': name, 'guideUrl': URLS[url_key],
            'verificationStatus': verification, 'groups': [], 'notes': []}


def group(p, title):
    g = next((g for g in p['groups'] if g['title'] == title), None)
    if g is None:
        g = {'id': f"{p['id']}_{len(p['groups']) + 1:02}", 'title': title, 'requirements': []}
        p['groups'].append(g)
    return g


def add(g, text, issuer='', condition=None, kind='DOCUMENT_GROUP'):
    g['requirements'].append({'text': text, 'issuerText': issuer or None,
                              'conditionText': condition, 'kind': kind})


def build(source):
    OUT.mkdir(parents=True, exist_ok=True)
    raw = source.read_bytes()
    text = raw.decode('utf-8-sig')
    # Exact input bytes are retained independently of normalized data.
    (OUT / 'research_source.txt').write_bytes(raw)
    chunks = re.split(r'(?m)^- (.+)\s*$', text)
    sections = {key.strip(): value for key, value in zip(chunks[1::2], chunks[2::2])}
    insurers = []
    def insurer(key, name, aliases, profiles):
        row = {'id': key, 'name': name, 'aliases': aliases, 'profiles': profiles}
        insurers.append(row)
        return row

    sp = profile('property_travel', '재물/배상 여행', 'samsung_property', 'BODY_REVIEWED')
    sm = profile('medical_travel', '여행중 질병/상해', 'samsung_medical', 'USER_CONFIRMED')
    insurer('samsung', '삼성화재', ['삼성화재', '삼성화재해상보험'], [sp, sm])
    s = sections['삼성화재']
    titles = ['공통', '휴대품손해', '분실여권 재발급(해외여행)', '수하물 지연(해외여행)', '항공기 지연결항(해외여행)', '여행중단(해외여행)', '의료비 공통/추가']
    for n, (_, _, rows) in enumerate(tables(s)):
        p = sm if n == 6 else sp
        current = titles[n]
        condition = None
        for req, issuer_text in rows:
            if req in ('기본)', '필수)', '추가)'):
                condition = None
                if n == 6:
                    current = '추가로 필요할 수 있는 서류' if req == '추가)' else '공통서류'
                continue
            if req in ('도난)', '파손)', '[실손 보상형]', '[지수형]'):
                current = titles[n] + ' / ' + req.strip('[])')
                condition = None
                continue
            if req.startswith('[') and req.endswith(']'):
                condition = req[1:-1]
                continue
            g = group(p, current)
            add(g, req, issuer_text, condition, 'NOTICE' if req.startswith('※') else 'DOCUMENT_GROUP')
    sp['notes'] = ['홈페이지 청구 시 청구서/동의서와 피해품 내역서의 제출 생략 조건을 각 항목에서 확인한다.']
    for g in sm['groups']:
        additional = g['title'] == '추가로 필요할 수 있는 서류'
        g['documentCategory'] = 'ADDITIONAL' if additional else 'COMMON'
        g['displayOrder'] = 2 if additional else 1
        g['requiresInsurerConfirmation'] = additional
        if additional:
            g['notice'] = '아래 서류는 사고내용과 보상담보에 따라 추가로 필요할 수 있습니다. 실제 제출이 필요한 서류는 삼성화재에 확인해 주세요.'
        for item in g['requirements']:
            if additional:
                item['requirementType'] = 'CONDITIONAL'
                if '약값 발생 시' in item['text']:
                    item['conditionText'] = '약값이 발생한 경우. 실제 제출 필요 여부는 보험사 확인 필요.'
                elif item['conditionText']:
                    item['conditionText'] += '. 실제 제출 필요 여부는 보험사 확인 필요.'
                else:
                    item['conditionText'] = '사고내용과 보상담보에 따라 추가로 필요할 수 있음. 실제 제출 필요 여부는 보험사 확인 필요.'
    sm['presentationRules'] = ['공통서류를 먼저 안내하고 추가로 필요할 수 있는 서류를 별도 목록으로 안내한다.', '추가서류를 모든 사용자에게 필수인 것처럼 안내하지 않는다. 추가 목록에 보험사 확인 안내를 함께 표시한다.', '홈페이지 청구 시 공통서류 중 보험금 청구서 및 개인(신용)정보처리동의서의 별도 제출 생략 조건을 유지한다.']
    sm['notes'] = ['사용자 확인에 따라 보험금 청구서부터 여권 사본까지 공통서류, 진단서부터 사고내용 확인서류까지는 필요시 추가서류로 구분한다.', '삼성 여행 질병/상해 목록 및 공통·추가 구분은 사용자 확인 완료. 브라우저 개별 목록 확인 제한은 검증 이력으로만 유지한다.']

    kp = profile('overseas_travel', '해외여행', 'kb', 'BODY_REVIEWED')
    insurer('kb', 'KB손해보험', ['KB손해보험', 'KB손보', '케이비손해보험'], [kp])
    major = sub = ''
    for _, header, rows in tables(sections['kb손보']):
        for cells in rows:
            a, b, req, issuer_text, _ = cells
            issuer_text = issuer_text or cells[-1]
            if a:
                major, sub = a, ''
            if b:
                sub = b.replace('\n', '')
            g = group(kp, ' / '.join(filter(None, (major, sub))))
            if sub == '통원':
                # The exported cell merged a nested amount table. Keep each tier distinct.
                # Only the three bold amount headings are used, not mentions in notes.
                cleaned = re.sub(r'\s+', ' ', req)
                m = list(re.finditer(r'(?<!\S)(3만원 이하|3만원 초과 ~ 10만원 이하|10만원 초과)(?=-)', cleaned))
                if len(m) == 3:
                    kp['groups'].remove(g)
                    shared_notes = None
                    for j, match in enumerate(m):
                        tier = match.group(1)
                        body = cleaned[match.end():m[j+1].start() if j+1<len(m) else len(cleaned)].lstrip('- ')
                        if j == 2 and '※ 진료비세부내역서는' in body:
                            body, shared_notes = body.split('※ 진료비세부내역서는', 1)
                            shared_notes = '※ 진료비세부내역서는' + shared_notes
                        add(group(kp, '상해/질병 / 국내통원 / ' + tier), body, issuer_text, '국내 통원 및 청구금액 ' + tier)
                    if shared_notes:
                        add(group(kp, '상해/질병 / 국내통원 / 공통 유의사항'), shared_notes,
                            issuer_text, '국내 통원 전체 금액 구간', 'NOTICE')
                    continue
            add(g, req, issuer_text)
    kp['notes'] = ['해외 입원/통원과 국내입원/국내통원의 금액별 기준을 혼합하지 않는다.', '대체서류와 조건부 추가서류를 동시에 모두 필수로 안내하지 않는다.', '비급여 내역이 없는 경우 진료비세부내역서 생략 가능. 질병분류코드 및 표준영수증 조건을 유지한다.']

    mp = profile('overseas_travel', '해외여행자보험', 'meritz', 'BODY_REVIEWED')
    insurer('meritz', '메리츠화재', ['메리츠화재', '메리츠화재해상보험'], [mp])
    for _, header, rows in tables(sections['메리츠']):
        if len(header) != 4:
            continue
        for a, b, req, issuer_text in rows:
            add(group(mp, a + (' / ' + b if b != '—' else '')), req, issuer_text)
    mp['verificationEvidence'] = {'url': 'https://cmdown.meritzfire.com/manager/cm/document/supply_file_info_outtravel.pdf', 'pages': [6, 7], 'purpose': '내부 검증 출처. 사용자 응답에 양식 파일 링크로 포함하지 않는다.'}
    mp['notes'] = ['공식 페이지에서 연결된 PDF 6페이지의 해외여행자보험 표를 대조함.', '호적등본은 공식 PDF 및 사용자 확인에 따른 현재 홈페이지 표기를 그대로 유지한다. 자료 전사 검증에서 별도 대체서류 확인을 요구하지 않는다.', '금액과 관계없이 도난신고확인원 필요 문구는 같은 PDF 7페이지 도난사고 안내에 존재. 해외여행 표 자체의 별도 행은 아님.']

    hp = profile('travel', '여행', 'hyundai', 'BODY_REVIEWED_USER_CONFIRMED')
    insurer('hyundai', '현대해상', ['현대해상', '현대해상화재보험'], [hp])
    hs = sections['현대해상']
    for pos, header, rows in tables(hs):
        before = '\n'.join(hs.splitlines()[:pos])
        title = re.findall(r'(?m)^\s*## (.+)$', before)[-1]
        for cells in rows:
            if len(header) == 3:
                sub, req, issuer_text = cells
                title_full = title + (' / ' + sub if sub != '공통' else '')
            else:
                req, issuer_text = cells
                title_full = title
            add(group(hp, title_full), req, issuer_text)
    hp['notes'] = ['홈페이지/모바일웹/앱 청구 시 보험금청구서와 신분증 사본 제출 불필요.', '여행증명서 발급처 항공사 및 합의금 지급 확인서 발급처 구청은 공식 페이지와 일치하고 사용자 확인 완료. 원문 유지.']
    domestic_identity = {'text': '국내여행 시 신분증 사본(앞면)', 'issuerText': '개인',
        'conditionText': '국내여행. 홈페이지/모바일웹/모바일앱 청구 시 제출 불필요.',
        'kind': 'DOCUMENT_GROUP', 'origin': 'OFFICIAL_PAGE_ADDITION'}
    group(hp, '공통')['requirements'].append(domestic_identity)

    dp = profile('travel', '여행자보험 및 휴대품손해', 'db_travel', 'BODY_REVIEWED_USER_CONFIRMED')
    insurer('db', 'DB손해보험', ['DB손해보험', 'DB손보', '디비손해보험', '동부화재'], [dp])
    ds = sections['db손보']
    travel_start = ds.index('**여행자보험 및 휴대품손해**')
    major = sub = ''
    for _, _, rows in tables(ds[travel_start:]):
        for a, b, req, issuer_text, _ in rows:
            if a:
                major, sub = a, ''
            if b:
                sub = b
            add(group(dp, ' / '.join(filter(None, (major, sub)))), req, issuer_text)
    dp['notes'] = ['미성년자 추가서류의 주민등록등본 또는 가족은 공식 페이지와 사용자 확인에 따른 원문 그대로 유지한다. 가족관계증명서 등으로 임의 확장하지 않는다.', '잔존물 회수는 서류가 아닌 제출/처리 조치로 응답 시 분리한다.', '감가율·25% 감가 문구는 서류 수집 참고정보이며 보상금 판단 근거로 사용하지 않는다.']
    # General health/driver material is retained for research, never merged into travel.
    dump('db_non_travel_reference.json', {'automaticSelectionAllowed': False,
        'reason': '질병/상해·운전자·치아·태아 등 별도 상품 안내. 여행보험 공통서류에 합산 금지.',
        'sourceUrl': URLS['db_general'], 'sourceText': ds[:travel_start]})

    hw = sections['한화손보']
    split_at = hw.index('[](https://www.hwgeneralins.com')
    cp = profile('carrot_channel', '캐롯 해외여행보험 여행중사고', 'carrot', 'SITE_INSPECTION_BLOCKED')
    wp = profile('hanwha_travel', '한화손보 여행자보험', 'hanwha', 'FETCH_TIMEOUT')
    hanwha = insurer('hanwha', '한화손해보험', ['한화손해보험', '한화손보'], [wp])
    carrot = insurer('carrot', '캐롯손해보험', ['캐롯손해보험', '캐롯'], [cp])
    for company in (hanwha, carrot):
        company['dbInsurerName'] = company['name']
        company['identityVerification'] = {
            'verifiedAt': DATE, 'source': 'EC2 운영 DB policy_terms.insurer_name 읽기 전용 조회',
            'table': 'policy_terms', 'column': 'insurer_name'}
    cp['userContentConfirmation'] = '사용자 확인 완료. 지연비용 목록은 원문 순서대로 일반 안내하고 실제 제출서류는 보험사 확인 안내를 함께 제공한다.'
    cp['selectionConstraint'] = '연결 약관의 보험사명 캐롯손해보험에 매칭한다. 한화손해보험 자료와 합산 금지. 증권 인수사 표기가 한화일 때 이름만으로 캐롯에 매칭하지 않는다.'
    # The next profile heading precedes its link and must not attach to the last document.
    carrot_text = re.sub(r'\s+한화손보\s*$', '', hw[:split_at])
    parts = re.split(r'(?m)^\s*\*\*(.+?)\*\*\s*$', carrot_text)
    for title, body in zip(parts[1::2], parts[2::2]):
        lines = body.splitlines()
        blocks = []
        for line in lines:
            if re.match(r'^    - ', line):
                blocks.append(line.strip()[2:])
            elif blocks and line.strip():
                blocks[-1] += '\n' + line.strip()
        for block in blocks:
            add(group(cp, title), block)
    major = sub = ''
    for _, header, rows in tables(hw[split_at:]):
        if len(header) != 5:
            continue
        for a, b, c, req, issuer_text in rows:
            if a:
                major, sub = a, ''
            if b:
                sub = b
            cond = b if major == '공통서류' and c == '추가' else None
            add(group(wp, ' / '.join(filter(None, (major, sub)))), req, issuer_text, cond)
    wp['notes'] = ['홈페이지 시간·금액 기준은 일반 안내 원문 보존용이며 가입 상품의 보상 요건으로 단정하지 않는다.', '가이드 확인서·목격자 서류는 도난확인서의 부재 조건에 따른 대체 경로다. 모두 필수로 나열하지 않는다.', '한화 공식 페이지 응답 시간 초과로 첨부 자료와 Notion만 확인했다.']
    cp['notes'] = ['사용자가 제공한 이미지 4장 및 Notion 전사를 근거로 보관. 공식 페이지는 개발자 도구 실행 차단 안내로 본문 검증 불가.', '사용자 확인: 항공기 및 수하물 지연비용은 원문에서 개별 적용이 명시되지 않아 원문 목록 그대로 일반 안내한다. 임의로 사고별 서류를 분리하지 않는다.', '2박 이내 숙박비용은 원문에 서류명이 아닌 비용으로 표기되어 있어 임의로 영수증 문구를 추가하지 않았다.']

    for company in insurers:
        for p in company['profiles']:
            p['automaticSelectionAllowed'] = p['verificationStatus'] in ('BODY_REVIEWED', 'USER_CONFIRMED', 'BODY_REVIEWED_USER_CONFIRMED')
            p['reviewedAt'] = DATE
            for g in p['groups']:
                g['requiresConditionEvaluation'] = True
    payload = {'schemaVersion': 1, 'reviewedAt': DATE, 'researchPageUrl': NOTION,
        'sourceSha256': hashlib.sha256(raw).hexdigest(),
        'purpose': '여행보험 일반 청구서류 안내용 사전 자료. 보상 여부·금액·지급조건 판단용 아님.',
        'notice': '일반적인 청구서류 안내입니다. 가입 상품과 사고 내용에 따라 추가·대체 서류가 필요할 수 있으니 보험사 공식 필요서류 안내 및 담당자에게 최종 확인해 주세요.',
        'runtimeIntegrated': False,
        'selectionRules': ['보험사는 현재 증권/연결 약관에서 확인한다. 질문만으로 보험사를 추측하지 않는다.',
            '동일 보험사라도 프로필 및 국내/해외·사고 유형이 다른 목록은 합산하지 않는다.',
            '공통 그룹과 해당 유형만 선택하되 조건부/대체서류는 조건을 확인한다.',
            'requirements.text는 조건·예시를 포함하는 자료 블록이다. 각 블록을 필수 서류 한 건으로 취급하지 않는다.',
            '서류 양식 파일 및 다운로드 링크는 응답에 포함하지 않는다. 목록 전체의 guideUrl만 사용한다.',
            '미검증/의문 항목은 검증 기록을 확인하고 확정 안내를 보류한다.'],
        'insurers': insurers}
    dump('catalog.json', payload)
    print(json.dumps({'insurers': len(insurers), 'profiles': sum(len(i['profiles']) for i in insurers),
        'groups': sum(len(p['groups']) for i in insurers for p in i['profiles']),
        'requirementBlocks': sum(len(g['requirements']) for i in insurers for p in i['profiles'] for g in p['groups'])}, ensure_ascii=False))


if __name__ == '__main__':
    build(Path(sys.argv[1]))
