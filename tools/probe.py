"""새올 채용공고(05) 목록이 열리는지 한 번만 확인 (기관 목록 만들 때)

실행: python tools/probe.py eminwon.haeundae.go.kr
출력(JSON 한 줄): {"host", "ok", "legacy", "listed", "matched", "sample", "error"}
일반 TLS 로 실패하면 옛 TLS 로 한 번 더 시도. 요청은 최대 2회.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import crawler as c  # noqa: E402

host = sys.argv[1]
res = {'host': host, 'ok': False, 'legacy': False, 'listed': 0, 'matched': 0, 'sample': '', 'error': ''}
for legacy in (False, True):
    try:
        org = c.saeol_jobs_org('probe', 'probe', 'probe probe', host, legacy)
        f = c.Fetcher(legacy_hosts=[host] if legacy else [], verify=True)
        recs = c.LIST_FN['saeol'](f, org, org['boards'][0], 1, 10)
        kw = [r for r in recs if r.get('matched_keywords')]
        res.update(ok=True, legacy=legacy, listed=len(recs), matched=len(kw), sample=(kw[0]['title'][:40] if kw else ''), error='')
        break
    except Exception as e:  # noqa: BLE001
        res['error'] = f'{type(e).__name__}: {str(e)[:120]}'
print(json.dumps(res, ensure_ascii=False))
