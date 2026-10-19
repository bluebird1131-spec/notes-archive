"""Gov24 public service (benefit) list -> data/benefits.json (official open API, data.go.kr 15113968).

Run: DATA_GO_KR_KEY=... python tools/fetch_benefits.py
Keeps services for individuals/households/small businesses in welfare-type fields, trims long text,
drops volatile fields (view count) so the file only changes when services change. Never prints the key.
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
FIELDS = {'생활안정', '주거·자립', '보육·교육', '보건·의료', '임신·출산', '고용·창업', '보호·돌봄'}


def page(n):
    q = up.urlencode({'page': n, 'perPage': 1000, 'serviceKey': KEY})
    for attempt in range(3):
        try:
            req = urllib.request.Request(f'{URL}?{q}', headers={'Accept': 'application/json'})
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


rows, n = [], 1
while True:
    d = page(n)
    rows += d.get('data') or []
    if len(rows) >= d.get('totalCount', 0) or not d.get('data'):
        break
    n += 1
out = []
for r in rows:
    users = r.get('사용자구분') or ''
    if r.get('서비스분야') not in FIELDS or not re.search(r'개인|가구|소상공인', users):
        continue
    out.append({
        'id': r.get('서비스ID'), 'name': clip(r.get('서비스명'), 80), 'summary': clip(r.get('서비스목적요약'), 160),
        'target': clip(r.get('지원대상'), 260), 'content': clip(r.get('지원내용'), 260), 'deadline': clip(r.get('신청기한'), 80),
        'method': clip(r.get('신청방법'), 60).replace('||', ', '), 'org': clip(r.get('소관기관명'), 40),
        'orgType': r.get('소관기관유형') or '', 'dept': clip(r.get('부서명'), 30), 'phone': clip(r.get('전화문의'), 60),
        'field': r.get('서비스분야') or '', 'users': users.replace('||', ', '), 'type': (r.get('지원유형') or '').replace('||', ', '),
        'url': r.get('상세조회URL') or '', 'updated': (r.get('수정일시') or '')[:8],
    })
out.sort(key=lambda x: x['id'] or '')
Path('data').mkdir(exist_ok=True)
Path('data/benefits.json').write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
print(f'services {len(rows)} -> kept {len(out)}, {Path("data/benefits.json").stat().st_size // 1024} KB')
