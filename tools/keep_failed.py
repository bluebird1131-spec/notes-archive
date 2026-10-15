"""이번 수집에서 실패한 기관은 지난 결과를 그대로 이어 붙임 (해외 서버에서 일시적으로 막혀도 공고가 사라지지 않게)

실행: python tools/keep_failed.py <지난 raw_notices.json>
- scripts/out/crawl_log.json 에서 실패(ok=false)한 기관을 찾아
- 지난 결과 중 그 기관 공고를 scripts/out/raw_notices.json 에 다시 넣음
- data/last_sync.json 에 실행 시각·기관별 결과를 남김 (매일 커밋이 생겨 60일 무활동으로 예약이 꺼지는 일도 막음)
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path('scripts/out')
prev_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
raw = json.loads((OUT / 'raw_notices.json').read_text(encoding='utf-8')) if (OUT / 'raw_notices.json').exists() else []
log = json.loads((OUT / 'crawl_log.json').read_text(encoding='utf-8')) if (OUT / 'crawl_log.json').exists() else {}
orgs = log.get('orgs', log if isinstance(log, list) else [])
failed = {o.get('source_org') for o in orgs if isinstance(o, dict) and not o.get('ok', True)}

kept = 0
if prev_path and prev_path.exists() and failed:
    prev = json.loads(prev_path.read_text(encoding='utf-8'))
    have = {n.get('detail_url') for n in raw}
    for n in prev:
        if n.get('source_org') in failed and n.get('detail_url') not in have:
            raw.append(n)
            kept += 1
    (OUT / 'raw_notices.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')

now = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds')
summary = {'at': now, 'notices': len(raw), 'failed_orgs': sorted(x for x in failed if x), 'kept_from_previous': kept}
Path('data').mkdir(exist_ok=True)
Path('data/last_sync.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(summary)
