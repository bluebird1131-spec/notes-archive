"""Gov24 public service (benefit) list -> data/benefits.json (official open API, data.go.kr 15113968).

Run: DATA_GO_KR_KEY=... python tools/fetch_benefits.py
Keeps services for individuals/households/small businesses in welfare-type fields, trims long text,
keeps the view count rounded to 2 significant digits (for popularity order) so daily changes stay small. Never prints the key.
"""
import json
import os
import re
import sys
import time
import urllib.parse as up
import urllib.request
from pathlib import Path

KEY = os.environ.get('DATA_GO_KR_KEY', '').strip()
if not KEY:
    sys.exit('DATA_GO_KR_KEY missing')
URL = 'https://api.odcloud.kr/api/gov24/v3/serviceList'
COND_URL = 'https://api.odcloud.kr/api/gov24/v3/supportConditions'
# supportConditions 코드 (공식 명세 infuser.odcloud.kr/api/stages/44436/api-docs 의 설명 그대로)
PERSON = {'JA0301': '예비부모·난임', 'JA0302': '임산부', 'JA0303': '출산·입양', 'JA0313': '농업인', 'JA0314': '어업인',
          'JA0315': '축산업인', 'JA0316': '임업인', 'JA0317': '초등학생', 'JA0318': '중학생', 'JA0319': '고등학생',
          'JA0320': '대학생·대학원생', 'JA0326': '근로자·직장인', 'JA0327': '구직자·실업자', 'JA0328': '장애인',
          'JA0329': '국가보훈대상자', 'JA0330': '질병·질환자'}  # JA0322 해당사항없음
HOUSE = {'JA0401': '다문화가족', 'JA0402': '북한이탈주민', 'JA0403': '한부모·조손가정', 'JA0404': '1인가구',
         'JA0411': '다자녀가구', 'JA0412': '무주택세대', 'JA0413': '신규전입', 'JA0414': '확대가족'}  # JA0410 해당사항없음
BIZ = {'JA1101': '예비창업자', 'JA1102': '영업 중', 'JA1103': '생계곤란·폐업예정'}
INCOME = [('JA0201', 50), ('JA0202', 75), ('JA0203', 100), ('JA0204', 200), ('JA0205', None)]
FIELDS = {'생활안정', '주거·자립', '보육·교육', '보건·의료', '임신·출산', '고용·창업', '보호·돌봄'}


def page(n, url=URL):
    q = up.urlencode({'page': n, 'perPage': 1000, 'serviceKey': KEY})
    for attempt in range(3):
        try:
            req = urllib.request.Request(f'{url}?{q}', headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:  # noqa: BLE001
            print(f'page {n} try {attempt + 1}: {type(e).__name__}', flush=True)
            time.sleep(10 * (attempt + 1))
    raise SystemExit(f'page {n} failed')


def clip(s, n):
    s = re.sub(r'\s+', ' ', str(s or '').replace('\r', ' ').replace('\n', ' ')).strip()
    s = '' if s in ('None', '해당없음') else s
    return s if len(s) <= n else s[: n - 1].rstrip() + '…'


def fetch_all(url):
    rows, n = [], 1
    while True:
        d = page(n, url)
        rows += d.get('data') or []
        if len(rows) >= d.get('totalCount', 0) or not d.get('data'):
            return rows
        n += 1


def conditions(c):
    """지원 조건 → 나이 범위, 소득 상한(중위 %), 대상 표시. '해당사항없음'이 Y 이면 누구나 받을 수 있어 대상 표시를 붙이지 않음"""
    if not c:
        return None, None, []
    def num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0
    lo, hi = num(c.get('JA0110')), num(c.get('JA0111'))
    age = [lo or None, hi if 0 < hi < 100 else None]
    age = age if any(age) else None
    inc = [c.get(k) == 'Y' for k, _ in INCOME]
    income = None
    if any(inc) and not all(inc):
        top = max(i for i, on in enumerate(inc) if on)
        income = INCOME[top][1]
    tags = []
    for group, none_code in ((PERSON, 'JA0322'), (HOUSE, 'JA0410')):
        on = [label for code, label in group.items() if c.get(code) == 'Y']
        if c.get(none_code) != 'Y' and 0 < len(on) < len(group):
            tags += on
    on = [label for code, label in BIZ.items() if c.get(code) == 'Y']
    if 0 < len(on) < len(BIZ):
        tags += on
    return age, income, tags


rows = fetch_all(URL)
conds = {c.get('서비스ID'): c for c in fetch_all(COND_URL)}
out = []
for r in rows:
    users = r.get('사용자구분') or ''
    if r.get('서비스분야') not in FIELDS or not re.search(r'개인|가구|소상공인', users):
        continue
    age, income, tags = conditions(conds.get(r.get('서비스ID')))
    out.append({
        'age': age, 'income': income, 'tags': tags,
        'id': r.get('서비스ID'), 'name': clip(r.get('서비스명'), 80), 'summary': clip(r.get('서비스목적요약'), 160),
        'target': clip(r.get('지원대상'), 260), 'content': clip(r.get('지원내용'), 260), 'deadline': clip(r.get('신청기한'), 80),
        'method': clip(r.get('신청방법'), 60).replace('||', ', '), 'org': clip(r.get('소관기관명'), 40),
        'orgType': r.get('소관기관유형') or '', 'dept': clip(r.get('부서명'), 30), 'phone': clip(r.get('전화문의'), 60),
        'field': r.get('서비스분야') or '', 'users': users.replace('||', ', '), 'type': (r.get('지원유형') or '').replace('||', ', '),
        'url': r.get('상세조회URL') or '', 'updated': (r.get('수정일시') or '')[:8],
        # 많이 찾는 순서로 보여 주려고 조회수를 두 자리 유효숫자로만 남김 (매일 조금씩 바뀌는 값이 파일을 흔들지 않게)
        'views': int(float(f"{int(r.get('조회수') or 0):.2g}")),
    })
out.sort(key=lambda x: x['id'] or '')
Path('data').mkdir(exist_ok=True)
Path('data/benefits.json').write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
print(f'services {len(rows)} (conditions {len(conds)}) -> kept {len(out)}, tagged {sum(1 for o in out if o["tags"])}, age {sum(1 for o in out if o["age"])}, {Path("data/benefits.json").stat().st_size // 1024} KB')
