"""국내 PC 에서 해외 서버(GitHub Actions)가 막힌 기관만 수집해 data/domestic_raw.json 으로 올림

실행: python tools/domestic_sync.py [--push]
- 최신 data/crawl_log.json (매일 Actions 결과)에서 실패한 기관만 골라 수집 (상세·첨부 포함)
- 결과: data/domestic_raw.json = {"at": 실행 시각, "orgs": [기관명], "notices": [...]}
- 다음 Actions 실행이 이 파일을 지난 결과보다 먼저 이어 붙임 (36시간이 지난 파일은 쓰지 않음)
- --push: git pull → 커밋 → push
"""
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
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
# 해외에서 막히는 기관은 날마다 조금씩 달라짐(연속 두 번 겹치는 건 30곳 안팎) → 최근 4번 수집에서 한 번이라도 실패한 기관을 모두 모음
hist = subprocess.run(['git', '-C', str(ROOT), 'log', '--format=%h', '-4', '--', 'data/last_sync.json'],
                      capture_output=True, text=True).stdout.split()
for h in hist:
    shown = subprocess.run(['git', '-C', str(ROOT), 'show', f'{h}:data/last_sync.json'], capture_output=True, encoding='utf-8')
    try:
        failed |= set(json.loads(shown.stdout).get('failed_orgs', []))
    except ValueError:
        pass
orgs = list(c.ORGS)
for f in sorted((ROOT / 'orgs').glob('*.json')):
    orgs += c.load_org_file(f)
targets = [o for o in orgs if o['source_org'] in failed]
print(f'최근 4번 수집에서 해외 실패한 기관 {len(failed)}곳 중 수집 대상 {len(targets)}곳')

# 서로 다른 기관 서버를 동시에 WORKERS 곳씩 (기관 하나 안에서는 요청 사이 1.5~3초 쉬는 것 그대로라 기관 부담은 같음)
WORKERS = 6
started = datetime.now(c.KST)
c.ca_bundle()  # 인증서 목록 파일을 스레드 시작 전에 한 번 만듦
with ThreadPoolExecutor(WORKERS) as ex:
    results = list(ex.map(lambda o: c.run_org(o, 1, 10, True, True), targets))
notices, ok = [], []
for org, (kept, lg) in zip(targets, results):
    if lg['ok']:
        notices += kept
        ok.append(org['source_org'])
prev = ROOT / 'data/raw_notices.json'
reuse = {n.get('detail_url'): n for n in json.loads(prev.read_text(encoding='utf-8')) if n.get('attachment_text')} if prev.exists() else {}
# 첨부도 기관별로 나눠 동시에
groups = {}
for n in notices:
    groups.setdefault(n['source_org'], []).append(n)
with ThreadPoolExecutor(WORKERS) as ex:
    sts = list(ex.map(lambda g: c.run_attachments(g, True, reuse=reuse), groups.values()))
st = {k: sum(s[k] for s in sts) for k in (sts[0] if sts else {})}
if st:
    c.print_attach_summary(st)
print(f'걸린 시간: {(datetime.now(c.KST) - started).seconds // 60}분')

out = ROOT / 'data/domestic_raw.json'
out.write_text(json.dumps({'at': datetime.now(c.KST).isoformat(timespec='seconds'), 'orgs': ok, 'notices': notices},
                          ensure_ascii=False, indent=1), encoding='utf-8')
print(f'저장: {out} (기관 {len(ok)}곳, 공고 {len(notices)}건)')
if push:
    git('add', 'data/domestic_raw.json')
    if subprocess.run(['git', '-C', str(ROOT), 'diff', '--cached', '--quiet']).returncode:
        git('commit', '-q', '-m', f"local {datetime.now(c.KST):%Y-%m-%d %H:%M}")
        git('pull', '-q', '--rebase')  # 수집하는 동안 Actions 가 올린 커밋이 있을 수 있음
        git('push', '-q')
        print('올림 완료')
