"""Daily collection health check -> data/health.json

Run after merge/keep_failed (sync.yml). Lists boards that need a human look:
- failed: org failed abroad and had no domestic result either
- stale:  board lists only old posts (newest post older than STALE_DAYS) — the org may have moved its job notices
- empty:  board returned no rows at all
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STALE_DAYS = 30
KST = timezone(timedelta(hours=9))
out = Path('scripts/out')
log = json.loads((out / 'crawl_log.json').read_text(encoding='utf-8'))
raw = json.loads((out / 'raw_notices.json').read_text(encoding='utf-8'))
sync = {}
p = Path('data/last_sync.json')
if p.exists():
    sync = json.loads(p.read_text(encoding='utf-8'))
domestic = set(sync.get('domestic_used') or [])

newest_raw = {}
for r in raw:
    d = r.get('posted_at') or ''
    if d > newest_raw.get(r['source_org'], ''):
        newest_raw[r['source_org']] = d

cut = (datetime.now(KST) - timedelta(days=STALE_DAYS)).strftime('%Y-%m-%d')
failed, stale, empty = [], [], []
for o in log.get('orgs', []):
    name = o['source_org']
    if not o.get('ok') and name not in domestic:
        failed.append({'org': name, 'error': (o.get('error') or next((b['error'] for b in o.get('boards', []) if b.get('error')), ''))[:120]})
    for b in o.get('boards', []):
        if b.get('error'):
            continue
        if b.get('listed', 0) == 0:
            empty.append({'org': name, 'board': b['board']})
        elif b.get('newest') and b['newest'] < cut:
            stale.append({'org': name, 'board': b['board'], 'newest': b['newest']})
# 해외에서 막혀 게시판 정보가 없는 기관은 국내 결과의 최신 글로 대신 봄
for o in log.get('orgs', []):
    name = o['source_org']
    if not o.get('ok') and name in domestic and newest_raw.get(name, '') and newest_raw[name] < cut:
        stale.append({'org': name, 'board': '(domestic)', 'newest': newest_raw[name]})

doc = {'at': datetime.now(KST).isoformat(timespec='seconds'), 'orgs': len(log.get('orgs', [])),
       'failed': failed, 'stale': stale, 'empty': empty}
Path('data/health.json').write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
print(f"health: failed {len(failed)}, stale {len(stale)}, empty {len(empty)}")
for x in failed + stale + empty:
    print('  ', x)
