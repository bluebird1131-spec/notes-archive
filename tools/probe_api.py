"""Open API probe: prints response structure only (keys, counts, short samples). Never prints the key."""
import json
import os
import sys
import urllib.parse as up
import urllib.request

KEY = os.environ.get('DATA_GO_KR_KEY', '').strip()


def get(url, params):
    q = up.urlencode({**params, 'serviceKey': KEY})
    req = urllib.request.Request(f'{url}?{q}', headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except Exception as e:  # noqa: BLE001
        body = getattr(e, 'read', lambda: b'')().decode('utf-8', 'replace')
        return getattr(e, 'code', 0), (str(e) + ' ' + body)[:500]


def show(name, url, params):
    st, body = get(url, params)
    print(f'===== {name} HTTP {st} len={len(body)}')
    try:
        d = json.loads(body)
    except ValueError:
        print(body[:800])
        return
    print('top keys:', list(d)[:20] if isinstance(d, dict) else type(d))
    rows = d.get('data') if isinstance(d, dict) else None
    if isinstance(d, dict):
        print({k: v for k, v in d.items() if k != 'data'})
    if rows:
        print('fields:', list(rows[0].keys()))
        for r in rows[:2]:
            print(json.dumps({k: (str(v)[:120]) for k, v in r.items()}, ensure_ascii=False))


if not KEY:
    sys.exit('no key')
B = 'https://api.odcloud.kr/api/gov24/v3'
show('serviceList', f'{B}/serviceList', {'page': 1, 'perPage': 2})
show('supportConditions', f'{B}/supportConditions', {'page': 1, 'perPage': 1})
show('serviceDetail', f'{B}/serviceDetail', {'page': 1, 'perPage': 1})
show('serviceList 울산 남구', f'{B}/serviceList', {'page': 1, 'perPage': 2, 'cond[소관기관명::LIKE]': '울산광역시 남구'})
