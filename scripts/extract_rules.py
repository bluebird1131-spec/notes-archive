"""구·군청 채용 공고문(본문+hwpx 첨부)에서 정해진 문구를 규칙으로 뽑기 — AI 없이

뽑는 칸: 접수기간(apply_start/end), 근무기간, 일급·월급·시급, 채용인원, 접수처, 접수방법, 제출서류
뽑은 값마다 근거 줄(evidence)을 그대로 남김 → 원문에 없는 숫자는 생기지 않음
못 뽑은 칸은 None

실행:
  python scripts/extract_rules.py                 # raw_notices.json → scripts/out/rules.json
  python scripts/extract_rules.py --eval          # 기준값(scripts/out/structured.json)과 칸별 일치율 비교
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

RAW = Path('scripts/out/raw_notices.json')
OUT = Path('scripts/out/rules.json')
TRUTH = Path('scripts/out/structured.json')

SP = r'[\s ]*'
# 2026. 10. 14. / 2026.10.14 / ’26. 10. 14 / `26. 10. 14 / 2026년 10월 14일
FULL = re.compile(r"(?:(20\d{2})|[’‘'`＇](\d{2}))" + SP + r"[.년]" + SP + r"(\d{1,2})" + SP + r"[.월]" + SP + r"(\d{1,2})")
# 26. 10. 20. (따옴표 없는 두 자리 연도, 10-27 철원 군부대 공고) — 공고 연도 ±1년일 때만 연도로 봄
BARE = re.compile(r"(?<![\d.])(\d{2})" + SP + r"\." + SP + r"(\d{1,2})" + SP + r"\." + SP + r"(\d{1,2})(?!\d)")
SHORT = re.compile(r"(?<![\d.])(\d{1,2})" + SP + r"[.월]" + SP + r"(\d{1,2})(?!\d)")
# 시각("~ 18:00")은 날짜가 아님 → 뒤에 콜론이 오면 제외
DAY_ONLY = re.compile(r"[~∼～]" + SP + r"(\d{1,2})" + SP + r"[.일]?(?!" + SP + r"[\d.월:])")


def dates_in(text, base_year):
    """줄 안의 날짜를 차례로. 연도가 없는 날짜는 앞 날짜의 연도를 이어 씀"""
    out, year, pos = [], base_year, 0
    spans = []
    for m in FULL.finditer(text):
        y = int(m.group(1)) if m.group(1) else 2000 + int(m.group(2))
        spans.append((m.start(), m.end(), y, int(m.group(3)), int(m.group(4))))
    for m in BARE.finditer(text):
        y = 2000 + int(m.group(1))
        if abs(y - base_year) <= 1 and not any(s <= m.start() < e for s, e, *_ in spans):
            spans.append((m.start(), m.end(), y, int(m.group(2)), int(m.group(3))))
    for m in SHORT.finditer(text):
        if any(s <= m.start() < e for s, e, *_ in spans):
            continue
        spans.append((m.start(), m.end(), None, int(m.group(1)), int(m.group(2))))
    for m in DAY_ONLY.finditer(text):
        if any(s <= m.start(1) < e for s, e, *_ in spans):
            continue
        spans.append((m.start(1), m.end(1), None, 0, int(m.group(1))))  # 월 0 = 앞 날짜의 월
    month = None
    for s, e, y, mo, d in sorted(spans):
        if mo == 0:
            if month is None:
                continue
            mo = month
        if y:
            year = y
        elif month is not None and mo < month:
            year += 1  # '2026. 12. 30. ~ 1. 2.' 처럼 연도 없이 해가 바뀜
        try:
            date(year, mo, d)  # 2월 30일 같은 없는 날짜는 버림
        except ValueError:
            continue
        month = mo
        out.append(f'{year:04d}-{mo:02d}-{d:02d}')
    return out


TILDE = r'[~∼～]'
# 날짜가 하나뿐일 때: 마감일만 적힘('10. 28.(수) 18:00까지', '공고일 ~ 10. 23.') → 시작일 없음(게시일로)
DEADLINE = re.compile(r'^[^~∼～]*?(까지|限|\d{1,2}\s*:\s*\d{2}\s*한)')
# 시작일만 적힘('10. 8.(목) ~ 채용시까지') → 마감일 없음(원문 확인)
OPEN_END = re.compile(TILDE + r'\s*(채용|충원|선발|모집\s*(완료|시)|예산|소진|$)')
# 다음 줄이 날짜나 '~'로 시작하면 이어 봄 ('5. 접수방법' 같은 번호 줄은 아님)
STARTS_DATE = re.compile(r"^\s*(?:" + TILDE + r"|(?:20\d{2}|[’‘'`＇]\d{2})" + SP + r"[.년]|\d{1,2}" + SP + r"[.월]" + SP + r"\d)")


def period_of(lines, i, year, at=0):
    """접수 낱말(at 글자 위치) 뒤에서 두 날짜를 먼저 찾고, 모자라면 다음 줄을 세 줄까지 이어 봄
    (표 칸이 '2026. 10. 23.(금)' / '~' / '10. 27.(화)'처럼 줄마다 나뉨). 종료일이 시작일보다 앞이면 버림"""
    line = lines[i][at:]
    ds = dates_in(line, year)
    used = [line]  # 근거도 낱말부터 (검수가 근거 줄의 날짜로 다시 대조함)
    j = i + 1
    while len(ds) < 2 and j < len(lines) and j <= i + 3 and (STARTS_DATE.match(lines[j]) or re.search(TILDE + r'\s*$', line)):
        line = line + ' ' + lines[j]
        ds = dates_in(line, year)
        used.append(lines[j])
        j += 1
    if len(used) > 1 and not re.search(TILDE, line):
        ds = ds[:1]  # 줄을 이었는데 '~'가 없으면 둘째 날짜는 다른 칸(면접일 등)일 수 있음
    if not ds:
        return None
    if len(ds) == 1:
        m = next(FULL.finditer(line), None) or next(BARE.finditer(line), None) or next(SHORT.finditer(line), None)
        before, rest = (line[:m.start()], line[m.end():]) if m else ('', '')
        if OPEN_END.search(rest):
            return ds[0], None, ' / '.join(used)
        if re.search(TILDE, before) or DEADLINE.search(rest):
            return None, ds[0], ' / '.join(used)
    start, end = ds[0], ds[1] if len(ds) > 1 else ds[0]
    if end < start:
        return None
    return start, end, ' / '.join(used)


def lines_of(text):
    return [l.strip() for l in text.splitlines() if l.strip()]


def find_line(lines, pattern, span=1, skip=None):
    rx = re.compile(pattern)
    for i, l in enumerate(lines):
        if rx.search(l) and not (skip and re.search(skip, l)):
            return ' '.join(lines[i:i + span]), i
    return None, -1


def won(s):
    return int(s.replace(',', ''))


def extract(n):
    text = '\n'.join(filter(None, [n.get('body_text'), n.get('attachment_text')]))
    lines = lines_of(text)
    year = int((n.get('posted_at') or '2026')[:4])
    r, ev = {}, {}

    # 접수기간: '공고기간'은 빼고 접수·모집 기간 우선. '공고(접수)기간'·'접수(취소)기간'·'원 서 접 수'도 봄 (10-27)
    for pat in [r'접\s*수\s*\)?\s*(?:\([^)]{1,6}\)\s*)?기\s*간', r'원\s*서\s*접\s*수', r'서\s*류\s*접\s*수',
                r'모\s*집\s*기\s*간', r'접\s*수\s*일\s*시', r'신\s*청\s*기\s*간']:
        rx = re.compile(pat)
        # '채용서류는 2027. 2. 1.까지 채용서류 반환청구서를 응시원서 접수처에 제출' 같은 서류 반환 안내는 접수기간이 아님 (10-24 영도구청)
        hits = [(i, m.start()) for i, l in enumerate(lines) if (m := rx.search(l)) and not re.search(r'반환\s*청구', l)]
        got = None
        # 낱말 뒤의 날짜만 봄('1. 1차 서류접수 2026. 10. 12.'의 '1. 1'은 날짜 아님). 처음 걸린 줄은 날짜가 낱말 앞에만 있으면
        # 줄 전체를 봄('나. 2026. 10. 22. ~ 10. 23. : 응시원서 접수'). 날짜가 없으면('4. 접수기간 및 방법' 같은 머리 줄) 다음에 걸린 줄들을 봄
        for k, (i, at) in enumerate(hits):
            front_only = k == 0 and not dates_in(lines[i][at:], year) and dates_in(lines[i], year)
            got = period_of(lines, i, year, 0 if front_only else at)
            if got:
                break
        if got:
            r['apply_start'], r['apply_end'], ev['period'] = got
            if not got[1]:  # 시작일만 적힌 공고
                m = OPEN_END.search(got[2])
                if m and re.search(r'예산|소진', m.group(1)):
                    r['apply_note'] = '예산 소진 시까지'
                elif m and m.group(1):
                    r['apply_note'] = '채용 시까지'
            break

    # 근무기간
    line, i = find_line(lines, r'(근무|계약|사업)\s*기간', span=2)
    if line:
        ds = dates_in(line, year)
        if len(ds) >= 2:
            r['work_start'], r['work_end'] = ds[0], ds[1]
            ev['work'] = lines[i]

    # 보수: 일급 → 월급 → 시급
    def pay_match(rx):
        for m in re.finditer(rx, text):
            before = text[max(0, m.start() - 25):m.start()]
            # 간식비·교통비 같은 실비나 시급(1만 원 안팎)을 일급으로 읽지 않게 2만 원 미만은 버림
            if not re.search(r'경비|급식|교통|식비|급량|간식|실비|여비|시간당', before + m.group(0)) and won(m.group(1)) >= 20_000:
                return m
        return None
    m = (pay_match(r'일\s*급\s*[:：]?\s*(?:[’‘\']?\d{2}\s*[:：]\s*)?([\d,]{5,})\s*원')
         or pay_match(r'1\s*일\s*(?:단가|임금|보수)[^\d]{0,40}?([\d,]{5,})\s*원')
         or pay_match(r'([\d,]{5,})\s*원\s*/\s*(?:1\s*)?일')
         or pay_match(r'(?:일\s*당|1일)\s*[:：]?\s*([\d,]{5,})\s*원'))
    if m:
        r['daily_pay'] = won(m.group(1))
        ev['pay'] = text[max(0, m.start() - 20):m.end() + 10].replace('\n', ' ')
        # 직무별로 일급이 여럿이면 범위로 (예: 관리원 85,440원, 청소원 42,720원)
        alls = {won(x) for x in re.findall(r'([\d,]{5,})\s*원', text[max(0, m.start() - 200):m.end() + 300])}
        alls = {v for v in alls if 20_000 <= v <= 200_000}
        if len(alls) > 1:
            r['daily_pay_min'], r['daily_pay_max'] = min(alls), max(alls)
    m = re.search(r'월\s*(?:급여|보수|임금)?\)?\s*[:：]?\s*(\d{2,3})\s*만\s*원', text) or re.search(r'월\s*(?:급여|보수|임금)\s*[:：]?\s*([\d,]{7,})\s*원', text)
    if m:
        v = m.group(1)
        r['monthly_pay'] = int(v) * 10_000 if len(v) <= 3 else won(v)
        ev.setdefault('pay', text[max(0, m.start() - 10):m.end() + 10].replace('\n', ' '))
    m = re.search(r'시\s*급\s*[:：]?\s*([\d,]{5,})\s*원', text)
    if m:
        r['hourly_pay'] = won(m.group(1))

    # 채용인원
    m = re.search(r'(?:채용|모집|선발)\s*인원\s*[:：]?\s*(?:총\s*)?(\d{1,3})\s*명', text) or re.search(r'(?<![\d.])(\d{1,3})\s*명\s*(?:\n|$)', text)
    if m:
        r['headcount'] = int(m.group(1))

    # 접수처·접수방법
    line, i = find_line(lines, r'접\s*수\s*처')
    if line:
        r['apply_place'] = re.sub(r'^.*?접\s*수\s*처\s*[:：]?\s*', '', line)[:60]
    line, i = find_line(lines, r'(접수|신청)\s*방법')
    method = (line or '') + ' ' + (r.get('apply_place') or '')
    if '행정복지센터' in method or '주민센터' in method or re.search(r'[가-힣]{1,4}(읍|면|동)\s*(사무소|산업팀|맞춤형|총무팀|민원)', method):
        r['apply_type'] = '동사무소 방문접수'
    elif re.search(r'방문|우편|등기|이메일|전자우편', method):
        r['apply_type'] = '구청 방문/우편'
    elif re.search(r'온라인|누리집\s*접수|인터넷', method):
        r['apply_type'] = '온라인 접수'

    # 제출서류: '제출서류'(또는 구비서류·공통사항) 다음 줄들에서 '1부'·서류명 줄을 모음
    _, i = find_line(lines, r'(제출|구비|응시)\s*서류|공통\s*사항')
    if i >= 0:
        docs = []
        for l in lines[i:i + 25]:
            if docs and re.match(r'^\d+\s*\.\s*[가-힣]', l) and '서' not in l[:6]:
                break  # 다음 큰 항목(예: '6. 선발방법')
            item = re.sub(r'^[\s\-‣∘○●▪•ㅇ①-⑳가-하]\s*[.)]?\s*', '', l)
            item = re.sub(r'^.*?(제출|구비)\s*서류\)?\s*[:：]?\s*', '', item)
            if re.search(r'(신청서|원서|이력서|소개서|초본|등본|확인서|증명서|동의서|사본|체크리스트|자격증|통장|신분증)', item) and len(item) < 80:
                docs.append(item.strip(' .'))
        counted = [d for d in docs if re.search(r'\d\s*(부|통)', d)]
        docs = counted if len(counted) >= 2 else [d for d in docs if not re.search(r'접수|선발|합격|점심|발견', d)]
        docs = [re.sub(r'\s*\[붙임\s*\d*\]|\s*각?\s*\d\s*(부|통).*$', '', d).strip(' ,.') for d in docs]
        docs = [d for d in docs if 2 <= len(d) <= 45]
        if docs:
            r['required_docs'] = ', '.join(dict.fromkeys(docs))[:300]
            ev['docs'] = lines[i]

    # 응시자격: 요약하지 않고 원문 줄을 그대로 (서식·결격사유 목록은 빼고 앞 몇 줄만)
    _, i = find_line(lines, r'(응시|지원|신청|참여|채용)\s*자격|자격\s*요건|^\s*\d+\s*\.\s*자\s*격')
    qual_lines = []
    if i >= 0:
        head = re.sub(r'^.*?(응시|지원|신청|참여|채용)?\s*자격\s*(요건)?\s*[:：]?\s*', '', lines[i]).strip()
        if len(head) >= 6:
            qual_lines.append(head)
        for l in lines[i + 1:i + 8]:
            if re.match(r'^\s*(\d+\s*\.|[□■]|【)', l) and qual_lines:
                break  # 다음 큰 항목
            if re.search(r'결격|피성년|파산|금고|징계|해고|형의|제한\s*요건|지원\s*제한', l):
                break  # 결격사유 목록부터는 생략 (원문에서 확인)
            item = re.sub(r'^[\s\-‣∘○●▪•ㅇ※❍]\s*|^[가-하]\s*[.)]\s*|^\(?\d\)\s*', '', l).strip()
            # 소제목('자격 기준', '(1) 응시자격')·규정 이름만 있는 줄·'붙임 참조'는 건너뜀
            if re.fullmatch(r'(응\s*시\s*)?자\s*격\s*(기\s*준|요\s*건)?\s*[:：]?', item) or re.fullmatch(r'「[^」]+」(\s*제\s*\d+\s*조.*)?', item)                     or re.search(r'붙임\s*참조|근로\s*조건', item):
                continue
            # 표 칸이 깨져 따옴표·빗금만 남은 줄('/'/'/)은 버림
            item = re.sub(r"(\s*[-’‘'\"]*\s*/\s*[’‘'\"]+)+\s*", ' ', item).strip(" -/'’‘")
            if 4 <= len(item) <= 120 and len(re.findall(r'[가-힣]', item)) >= 3:
                qual_lines.append(item)
            if len(qual_lines) >= 3:
                break
    # 표 머리글·소제목 조각('및 우대사항', '자 격 내 용', '공통요건' 등)은 자격 문구가 아니므로 뺌
    def heading(q):
        t = re.sub(r'\s', '', q)
        return t.startswith('및') or (len(t) <= 9 and re.search(r'우대|선발|자격|공통|세부|구분|내용|요건|기준|사항', t))
    qual_lines = [q for q in qual_lines if not heading(q)]
    # 자격이 '붙임 참조'로만 적힌 공고는 엉뚱한 줄이 따라오므로 비워 둠('원문 확인')
    if any(re.search(r'붙임\s*(공고문?\s*)?참조|공고문\s*참조', q) for q in qual_lines):
        qual_lines = []
    # 지원서·동의서 서식 안의 '자격' 칸을 잡은 경우(서명·귀하·이의 제기 문구)도 자격 문구가 아님
    if any(re.search(r'서\s*명\)|귀\s*하$|\(성명\)|이의를|제기하지\s*않|합격을\s*취소|\d{4}\s*년\s+월\s+일|동의합니다|확인합니다', q) for q in qual_lines):
        qual_lines = []
    qual_lines = [q for q in qual_lines if not re.search(r'공고\s*제\s*\d', q)]
    if qual_lines:
        r['qualification'] = ' / '.join(qual_lines)[:220]
        ev['qualification'] = lines[i]

    # 대상 표시: 응시자격·우대·가점 줄에 근거 문구가 있을 때만 (서식의 체크박스 등은 보지 않음)
    scope = ' '.join(qual_lines + [l for l in lines if re.search(r'우대|가점|우선\s*(선발|채용)', l) and len(l) < 150][:6])
    groups = []
    if re.search(r'청년|만\s*(1[5-9]|2\d)\s*세?\s*[~∼～\-]\s*(3\d)\s*세', scope):
        groups.append('청년(19~34세)')
    if re.search(r'중장년|신중년|(5\d)\s*세\s*이상', scope):
        groups.append('중장년(50세+)')
    if re.search(r'노인|어르신|(6\d)\s*세\s*이상', scope):
        groups.append('어르신(60세+)')
    if re.search(r'기초생활|수급자|차상위|한부모|취업\s*취약|저소득|장애인', scope):
        groups.append('무주택/취약계층')
    r['target_groups'] = groups
    if groups:
        ev['target'] = scope[:200]

    r['evidence'] = ev
    return r


def main():
    raw = json.load(open(RAW, encoding='utf-8'))
    targets = [n for n in raw if not n.get('excluded_reason') and not n.get('is_result_notice')]
    out = []
    for n in targets:
        r = extract(n)
        r['source_url'] = n['detail_url']
        r['title'] = n['title']
        out.append(r)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(out)}건 → {OUT}')
    if '--eval' in sys.argv and TRUTH.exists():
        evaluate(out)


def evaluate(out):
    truth = {t['source_url']: t for t in json.load(open(TRUTH, encoding='utf-8'))}
    fields = ['apply_start', 'apply_end', 'daily_pay', 'monthly_pay', 'apply_type', 'required_docs']
    stats = {f: [0, 0, 0] for f in fields}  # 맞음, 틀림, 못 뽑음
    misses = []
    for r in out:
        t = truth.get(r['source_url'])
        if not t:
            continue
        for f in fields:
            tv, rv = t.get(f), r.get(f)
            if f == 'daily_pay':
                mm = re.search(r'일급\s*([\d,]+)\s*원', t.get('benefit_summary', '')) or re.search(r'([\d,]{5,})\s*원', t.get('benefit_summary', ''))
                tv = int(mm.group(1).replace(',', '')) if mm else None
            if f == 'required_docs':
                if not rv:
                    stats[f][2] += 1
                    continue
                # 서류 이름 겹침 비율로 판단
                tw = set(re.findall(r'[가-힣]{3,}', tv or ''))
                rw = set(re.findall(r'[가-힣]{3,}', rv))
                ok = tw and len(tw & rw) / len(tw) >= 0.5
            else:
                if rv is None:
                    if tv is not None:
                        stats[f][2] += 1
                    else:
                        stats[f][0] += 1
                    continue
                ok = tv == rv
            stats[f][0 if ok else 1] += 1
            if not ok:
                misses.append((f, r['title'][:30], tv, rv))
    print('\n칸별 결과 (맞음 / 틀림 / 못 뽑음)')
    for f, (a, b, c) in stats.items():
        print(f'  {f:14s} {a:3d} / {b:3d} / {c:3d}')
    print('\n틀린 것')
    for m in misses:
        print('  ', m)


if __name__ == '__main__':
    main()
