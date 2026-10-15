"""수집 중인 기관 목록(data/org_index.json): 사이트의 지역 선택·'매일 확인하는 기관' 목록이 이 파일을 씀

실행: python tools/org_index.py
- 기관마다 sido, sigungu, name, url(채용공고 게시판), ok(이번 수집 성공 여부)
"""
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import crawler as c  # noqa: E402

log = Path('scripts/out/crawl_log.json')
ok = {o['source_org']: bool(o.get('ok')) for o in (json.loads(log.read_text(encoding='utf-8')).get('orgs', []) if log.exists() else [])}
orgs = list(c.ORGS)
for f in sorted(glob.glob(str(c.ORGS_DIR / '*.json'))):
    orgs += c.load_org_file(f)
rows = []
for o in orgs:
    sido, _, sigungu = o['region_sigungu'].partition(' ')
    rows.append({'sido': sido, 'sigungu': sigungu, 'name': o['source_org'],
                 'url': o['boards'][0]['board_url'], 'ok': ok.get(o['source_org'])})
Path('data').mkdir(exist_ok=True)
Path('data/org_index.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
print(f'기관 {len(rows)}곳 → data/org_index.json')
