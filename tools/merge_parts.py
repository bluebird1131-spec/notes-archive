"""시·도별로 나눠 돌린 수집 결과(part/raw_<묶음>.json, part/log_<묶음>.json)를 하나로 합침

실행: python tools/merge_parts.py part base,busan,seoul,...
- scripts/out/raw_notices.json, scripts/out/crawl_log.json 을 만듦
- 결과가 아예 없는 묶음(작업 자체가 실패)은 그 묶음의 기관을 모두 실패(ok=false)로 기록 → keep_failed.py 가 지난 결과를 이어 붙임
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import crawler as c  # noqa: E402

part = Path(sys.argv[1])
groups = [g for g in sys.argv[2].split(',') if g]
raw, logs, starts, ends = [], [], [], []
attach = {}
for g in groups:
    rp, lp = part / f'raw_{g}.json', part / f'log_{g}.json'
    if rp.exists() and lp.exists():
        raw += json.loads(rp.read_text(encoding='utf-8'))
        lg = json.loads(lp.read_text(encoding='utf-8'))
        logs += lg.get('orgs', [])
        starts.append(lg.get('started_at', ''))
        ends.append(lg.get('finished_at', ''))
        for k, v in (lg.get('attach') or {}).items():
            if isinstance(v, int):
                attach[k] = attach.get(k, 0) + v
        continue
    orgs = c.ORGS if g == 'base' else c.load_org_file(c.ORGS_DIR / f'{g}.json')
    print(f'[{g}] 결과 없음 → {len(orgs)}곳 실패 처리')
    logs += [{'org': o['key'], 'source_org': o['source_org'], 'boards': [], 'ok': False,
              'error': 'group job produced no output'} for o in orgs]

out = Path('scripts/out')
out.mkdir(parents=True, exist_ok=True)
(out / 'raw_notices.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
doc = {
    'started_at': min(starts) if starts else '',
    'finished_at': max(ends) if ends else '',
    'options': {'groups': groups},
    'total_kept': len(raw),
    'total_excluded': sum(1 for n in raw if n.get('excluded_reason')),
    'orgs': logs,
    'attach': attach,
}
(out / 'crawl_log.json').write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
ok = sum(1 for lg in logs if lg.get('ok'))
print(f'합침: 공고 {len(raw)}건, 기관 {len(logs)}곳 (성공 {ok}, 실패 {len(logs) - ok})')
