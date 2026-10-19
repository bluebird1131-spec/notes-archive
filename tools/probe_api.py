"""Open API probe: prints aggregate structure only. Never prints the key."""
import collections
import json
import os
import sys
import urllib.parse as up
import urllib.request

KEY = os.environ.get('DATA_GO_KR_KEY', '').strip()
if not KEY:
    sys.exit('no key')
B = 'https://api.odcloud.kr/api/gov24/v3'


def page(n, size):
    q = up.urlencode({'page': n, 'perPage': size, 'serviceKey': KEY})
    with urllib.request.urlopen(urllib.request.Request(f'{B}/serviceList?{q}', headers={'Accept': 'application/json'}), timeout=60) as r:
        return json.loads(r.read().decode('utf-8'))


rows, n = [], 1
while True:
    d = page(n, 1000)
    rows += d['data']
    print('page', n, len(d['data']), d['totalCount'], flush=True)
    if len(rows) >= d['totalCount'] or not d['data']:
        break
    n += 1
print('rows', len(rows))
C = collections.Counter
print('기관유형', C(r['소관기관유형'] for r in rows).most_common())
print('분야', C(r['서비스분야'] for r in rows).most_common())
print('사용자구분', C(r['사용자구분'] for r in rows).most_common(12))
print('지원유형', C(r['지원유형'] for r in rows).most_common(12))
print('신청기한', C((r['신청기한'] or '')[:12] for r in rows).most_common(15))
print('신청방법', C(r['신청방법'] for r in rows).most_common(10))
sido = C((r['소관기관명'] or '').split(' ')[0] for r in rows if r['소관기관유형'] in ('시군구', '광역자치단체', '시도'))
print('시도별', sido.most_common(20))
avg = sum(len(json.dumps(r, ensure_ascii=False)) for r in rows) / len(rows)
print('avg bytes', int(avg))
