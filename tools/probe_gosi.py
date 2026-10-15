"""새올 고시/공고(채용 05 가 아닌 구분) 목록에 채용 공고가 섞여 올라오는지 확인 (채용 게시판이 비어 있는 기관용)

실행: python tools/probe_gosi.py <host> [<host> ...]  (옛 TLS 는 host 앞에 ! 붙임)
출력(JSON 한 줄씩): host, listed, matched, newest, sample
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import crawler as c  # noqa: E402

CODES = '01,02,03,04,06'
for arg in sys.argv[1:]:
    legacy, host = arg.startswith('!'), arg.lstrip('!')
    res = {'host': host, 'listed': 0, 'matched': 0, 'newest': '', 'sample': [], 'error': ''}
    try:
        org = c.saeol_org('p', 'p', 'p p', host, CODES, '', legacy)
        board = org['boards'][1]
        f = c.Fetcher(legacy_hosts=[host] if legacy else [])
        recs = c.LIST_FN['saeol'](f, org, board, 1, 30)
        kw = [r for r in recs if r['matched_keywords'] and not r['excluded_reason']]
        res.update(listed=len(recs), matched=len(kw), newest=max((r['posted_at'] for r in recs), default=''),
                   sample=[r['title'][:30] for r in kw[:3]])
    except Exception as e:  # noqa: BLE001
        res['error'] = f'{type(e).__name__}: {str(e)[:100]}'
    print(json.dumps(res, ensure_ascii=False), flush=True)
    time.sleep(2)
