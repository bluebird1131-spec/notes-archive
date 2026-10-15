"""국내 PC 에서 해외 서버(GitHub Actions)가 막힌 기관만 수집해 data/domestic_raw.json 으로 올림

실행: python tools/domestic_sync.py [--push]
- 최신 data/crawl_log.json (매일 Actions 결과)에서 실패한 기관만 골라 수집 (상세·첨부 포함)
- 결과: data/domestic_raw.json = {"at": 실행 시각, "orgs": [기관명], "notices": [...]}
- 다음 Actions 실행이 이 파일을 지난 결과보다 먼저 이어 붙임 (36시간이 지난 파일은 쓰지 않음)
- --push: git pull → 커밋 → push
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import crawler as c  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
push = '--push' in sys.argv
git = lambda *a: subprocess.run(['git', '-C', str(ROOT), *a], check=True)  # noqa: E731
if push:
    git('pull', '-q', '--rebase')

log = json.loads((ROOT / 'data/crawl_log.json').read_text(encoding='utf-8'))
failed = {o['source_org'] for o in log.get('orgs', []) if not o.get('ok')}
orgs = list(c.ORGS)
for f in sorted((ROOT / 'orgs').glob('*.json')):
    orgs += c.load_org_file(f)
targets = [o for o in orgs if o['source_org'] in failed]
print(f'해외에서 실패한 기관 {len(failed)}곳 중 수집 대상 {len(targets)}곳')

notices, ok = [], []
for org in targets:
    print(f"[{org['key']}] 수집")
    kept, lg = c.run_org(org, 1, 10, True, True)
    if lg['ok']:
        notices += kept
        ok.append(org['source_org'])
prev = ROOT / 'data/raw_notices.json'
reuse = {n.get('detail_url'): n for n in json.loads(prev.read_text(encoding='utf-8')) if n.get('attachment_text')} if prev.exists() else {}
st = c.run_attachments(notices, True, reuse=reuse)
c.print_attach_summary(st)

out = ROOT / 'data/domestic_raw.json'
out.write_text(json.dumps({'at': datetime.now(c.KST).isoformat(timespec='seconds'), 'orgs': ok, 'notices': notices},
                          ensure_ascii=False, indent=1), encoding='utf-8')
print(f'저장: {out} (기관 {len(ok)}곳, 공고 {len(notices)}건)')
if push:
    git('add', 'data/domestic_raw.json')
    if subprocess.run(['git', '-C', str(ROOT), 'diff', '--cached', '--quiet']).returncode:
        git('commit', '-q', '-m', f"local {datetime.now(c.KST):%Y-%m-%d %H:%M}")
        git('push', '-q')
        print('올림 완료')
