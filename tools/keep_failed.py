"""이번 수집에서 실패한 기관은 국내 수집 결과나 지난 결과를 이어 붙임 (해외 서버에서 막혀도 공고가 사라지지 않게)

실행: python tools/keep_failed.py <지난 raw_notices.json>
- scripts/out/crawl_log.json 에서 실패(ok=false)한 기관을 찾아
- 1순위: data/domestic_raw.json (국내 PC 가 36시간 안에 수집한 결과, tools/domestic_sync.py)
- 2순위: 지난 결과 중 그 기관 공고 (게시 90일이 지난 공고는 버림)
- data/last_sync.json 에 실행 시각·기관별 결과를 남김 (매일 커밋이 생겨 60일 무활동으로 예약이 꺼지는 일도 막음)
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
OUT = Path('scripts/out')
now = datetime.now(KST)
prev_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
load = lambda p: json.loads(p.read_text(encoding='utf-8')) if p and p.exists() else None  # noqa: E731
raw = load(OUT / 'raw_notices.json') or []
log = load(OUT / 'crawl_log.json') or {}
failed = {o.get('source_org') for o in log.get('orgs', []) if isinstance(o, dict) and not o.get('ok', True)}

dom = load(Path('data/domestic_raw.json')) or {}
dom_fresh = bool(dom.get('at')) and now - datetime.fromisoformat(dom['at']) < timedelta(hours=36)
dom_orgs = set(dom.get('orgs', [])) & failed if dom_fresh else set()
old_cut = (now - timedelta(days=90)).strftime('%Y-%m-%d')

have = {n.get('detail_url') for n in raw}
kept_dom = kept_prev = 0
for n in dom.get('notices', []) if dom_fresh else []:
    if n.get('source_org') in dom_orgs and n.get('detail_url') not in have:
        raw.append(n)
        have.add(n.get('detail_url'))
        kept_dom += 1
for n in load(prev_path) or []:
    org = n.get('source_org')
    if org in failed and org not in dom_orgs and n.get('detail_url') not in have and (n.get('posted_at') or '9') >= old_cut:
        raw.append(n)
        have.add(n.get('detail_url'))
        kept_prev += 1
(OUT / 'raw_notices.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')

summary = {'at': now.isoformat(timespec='seconds'), 'notices': len(raw), 'failed_orgs': sorted(x for x in failed if x),
           'domestic_at': dom.get('at'), 'domestic_used': sorted(dom_orgs),
           'kept_from_domestic': kept_dom, 'kept_from_previous': kept_prev}
Path('data').mkdir(exist_ok=True)
Path('data/last_sync.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print({k: v for k, v in summary.items() if k not in ('failed_orgs', 'domestic_used')},
      f'실패 {len(failed)}곳, 국내 결과 사용 {len(dom_orgs)}곳')
