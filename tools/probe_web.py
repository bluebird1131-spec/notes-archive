"""누리집 게시판 설정("web") 시험: 목록 1쪽 + 키워드에 걸린 첫 글 상세 1건

실행: python tools/probe_web.py <orgs 파일> "<기관명>" '<web 설정 JSON>'
  설정 JSON 을 주면 파일의 설정 대신 그것으로 시험 (파일은 고치지 않음)
출력: 목록 건수·키워드 통과·날짜 예시, 상세 본문 앞부분·첨부 목록
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import crawler as c  # noqa: E402

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
path, name = sys.argv[1], sys.argv[2]
entry = next(e for e in json.loads(Path(path).read_text(encoding='utf-8')) if e['name'] == name)
entry = {**entry, 'status': 'ok', 'note': ''}
if len(sys.argv) > 3:
    entry['web'] = json.loads(sys.argv[3])
tmp = Path(c.OUT_DIR) / '_probe_org.json'
tmp.parent.mkdir(parents=True, exist_ok=True)
tmp.write_text(json.dumps([entry], ensure_ascii=False), encoding='utf-8')
org = c.load_org_file(tmp)[0]
f = c.Fetcher(legacy_hosts=[org['host']] if org['legacy_tls'] else [])
recs = c.LIST_FN[org['kind']](f, org, org['boards'][0], 1, 10)
kw = [r for r in recs if r['matched_keywords'] and not r['excluded_reason']]
print(f'목록 {len(recs)}건, 키워드 통과 {len(kw)}건')
for r in recs[:5]:
    print('  ', r['posted_at'], '|', r['title'][:50], '|', r['detail_url'][:100])
if kw:
    r = kw[0]
    c.DETAIL_FN[org['kind']](f.get(r['detail_url']), r, org['host'])
    print('상세:', r['title'][:60], '| 게시', r['posted_at'])
    print('본문 앞:', r['body_text'][:400].replace('\n', ' / '))
    print('첨부:', [(a['name'], a['url'][:90]) for a in r['attachments']])
