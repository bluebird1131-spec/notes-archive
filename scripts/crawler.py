#!/usr/bin/env python3
"""지자체 새올 채용공고·고시공고 게시판의 공개 공고를 모으는 수집기.

공개 게시물만 읽으며, 기관마다 요청 사이에 쉬는 시간을 둔다. 결과는 가공 전 원자료(raw).

실행:
    python scripts/crawler.py                      # 6개 기관, 게시판마다 1쪽
    python scripts/crawler.py --orgs 남구,중구 --pages 2
    python scripts/crawler.py --no-detail          # 목록만 (상세 요청 생략)
    python scripts/crawler.py --attach             # 수집 + 대상 공고의 .hwpx/.pdf 첨부 텍스트 추출
    python scripts/crawler.py --attach-only        # 저장된 파일에 첨부 텍스트만 채우기

출력:
    scripts/out/raw_notices.json   필터에 걸린 공고 목록 (제외 사유가 붙은 건 포함)
    scripts/out/crawl_log.json     기관·게시판별 요청 결과, 건수, 오류

의존성: requests, beautifulsoup4 (pip install --user requests beautifulsoup4)
       pdf 첨부 추출은 pypdf가 있을 때만 (없으면 pdf는 건너뜀)
"""
from __future__ import annotations

import argparse
import json
import random
import re
import ssl
import sys
import time
import urllib.parse as up
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import web_board  # noqa: E402
from requests.adapters import HTTPAdapter

KST = timezone(timedelta(hours=9))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
DELAY_RANGE = (1.5, 3.0)
TIMEOUT = 20
BODY_LIMIT = 3000
OUT_DIR = Path(__file__).resolve().parent / "out"

# ---------------------------------------------------------------------------
# 키워드 필터
# ---------------------------------------------------------------------------
KEYWORDS = [
    "공공근로", "지역공동체", "기간제", "단기", "행정복지센터", "동사무소",
    "조사원", "보조원", "환경정비", "일자리", "채용", "모집",
]
GENERIC_KEYWORDS = {"채용", "모집"}

# '채용'·'모집'만 걸린 건 중 명백히 공공일자리가 아닌 것
EXCLUDE_RULES = [
    (re.compile(r"공무원|임용시험|임기제|개방형\s*직위|필기시험|면접시험|임용\s*예정"),
     "공무원·공단 직원 임용시험 절차 (공공일자리 아님)"),
    (re.compile(r"위원|자문단|평가단|심사단|협의체"), "위원·자문단 모집 (일자리 아님)"),
    (re.compile(r"통장|반장|(^|[\s(])이장"), "통장·반장·이장 위촉 모집 (일자리 아님)"),
    (re.compile(r"교육생|수강생|참가자|참가팀|체험단|입주자|입주기업|입점|사업자|업체|기업|단체|후원|봉사|"
                r"서포터즈|기자단|모니터단|명예|공모|작품|선수|회원|임차인|분양|청약|감사인|회계법인|법무법인|감정평가"),
     "교육·참가자·사업자 모집 (일자리 아님)"),
    (re.compile(r"수탁|위탁|수행\s*기관|제공\s*기관|지원\s*사업|보조금|무료\s*개방|참여\s*(가구|업소|기관|학교)"),
     "사업 참여·위탁기관 모집 (일자리 아님)"),
]
# 모집이 아닌 결과 발표 성격의 공고 (제외하지 않고 표시만 한다)
RESULT_RE = re.compile(
    r"합격자|최종\s*합격|서류\s*(심사|전형)\s*(결과|합격)|면접\s*(대상|결과)|선정\s*결과|"
    r"결과\s*(공고|발표|알림)|선발\s*결과"
)


# 공백 제거 매칭은 '공단 기간제'→'단기' 같은 오탐을 내므로 원문 그대로 본다.
KEYWORD_RE = {k: re.compile(re.escape(k)) for k in KEYWORDS}
KEYWORD_RE["단기"] = re.compile(r"(?<!공)단기")  # '공단기간제' 등 제외


def match_keywords(title: str) -> list[str]:
    return [k for k in KEYWORDS if KEYWORD_RE[k].search(title)]


def excluded_reason(title: str, matched: list[str]) -> str | None:
    if not matched or not set(matched) <= GENERIC_KEYWORDS:
        return None
    for pattern, reason in EXCLUDE_RULES:
        if pattern.search(title):
            return reason
    return None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class LegacyTLSAdapter(HTTPAdapter):
    """옛 암호군(ECDHE-RSA-AES256-SHA)이나 짧은 DH 키만 지원하는 서버용.

    Python 3.13 기본값(SECLEVEL=2)으로는 handshake failure가 나므로 SECLEVEL을 낮춘다.
    인증서 검증은 그대로 유지한다.
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


# 중간 인증서를 보내지 않는 기관 서버용: certifi 기본 목록 + 빠진 중간 인증서(certs/extra_ca.pem, 공개 인증서)
# 인증서 검증은 끄지 않고, 검증에 쓸 목록만 넓힘
EXTRA_CA = Path(__file__).resolve().parent.parent / "certs" / "extra_ca.pem"


def ca_bundle() -> str | bool:
    import certifi
    if not EXTRA_CA.exists():
        return True
    out = OUT_DIR / "ca_bundle.pem"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(Path(certifi.where()).read_text(encoding="utf-8") + "\n" + EXTRA_CA.read_text(encoding="utf-8"),
                   encoding="utf-8")
    return str(out)


class Fetcher:
    def __init__(self, legacy_hosts=(), verify=True):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
        })
        for host in legacy_hosts:
            self.s.mount(f"https://{host}", LegacyTLSAdapter())
        self.verify = ca_bundle() if verify is True else verify
        self.count = 0
        self._last = 0.0

    def _wait(self):
        if self._last:
            gap = random.uniform(*DELAY_RANGE) - (time.time() - self._last)
            if gap > 0:
                time.sleep(gap)

    def _send(self, method, url, **kw) -> requests.Response:
        # 새올 서버는 가끔 20초 넘게 응답이 없다. 시간초과·연결오류만 한 번 더 시도한다.
        for attempt in (1, 2):
            self._wait()
            if attempt == 2:
                time.sleep(5)
            try:
                r = self.s.request(method, url, verify=self.verify,
                                   timeout=TIMEOUT * attempt, **kw)
                break
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 2:
                    raise
            finally:
                self._last = time.time()
                self.count += 1
        r.raise_for_status()
        return r

    def get_bytes(self, url, referer=None, max_bytes=None) -> bytes:
        """첨부 다운로드용. 울주 새올은 Referer(상세 페이지 URL)가 없으면 400을 준다."""
        headers = {"Accept": "*/*"}
        if referer:
            headers["Referer"] = referer
        r = self._send("GET", url, headers=headers)
        data = r.content
        if max_bytes and len(data) > max_bytes:
            raise ValueError(f"파일이 너무 큼 ({len(data):,} bytes)")
        return data

    def request(self, method, url, **kw) -> str:
        r = self._send(method, url, **kw)
        # 새올·홈페이지 모두 UTF-8. 헤더에 charset이 없을 때만 본문으로 추정한다.
        if "charset" not in r.headers.get("content-type", "").lower():
            r.encoding = r.apparent_encoding
        text = r.text
        return text.lstrip("\ufeff")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, data, **kw):
        return self.request("POST", url, data=data, **kw)


# ---------------------------------------------------------------------------
# 공통 도우미
# ---------------------------------------------------------------------------
def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def page_text(node) -> str:
    for t in node(["script", "style", "noscript"]):
        t.decompose()
    lines = [clean(x) for x in node.get_text("\n").split("\n")]
    return "\n".join(x for x in lines if x)


def norm_date(s: str) -> str:
    m = re.search(r"(\d{4})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else clean(s)


def encode_uri_query(params: dict) -> str:
    """JS encodeURI()와 같은 방식으로 쿼리를 만든다 (공백 %20, @()+ 등은 그대로).

    새올 FileDown.jsp 는 브라우저가 만드는 이 형태만 받는다. form 방식(+ 공백)은 400/무응답.
    """
    safe = "~@#$&()*!+=:;,.?/'"
    return "&".join(f"{k}={up.quote(v, safe=safe)}" for k, v in params.items())


def strip_ellipsis(title: str) -> tuple[str, bool]:
    t = clean(title)
    if t.endswith("...") or t.endswith(".."):
        return t.rstrip(". "), True
    return t, False


def make_record(org, board, *, title, notice_no, posted_at, dept, detail_url, key):
    matched = match_keywords(title)
    return {
        "source_org": org["source_org"],
        "region_sigungu": org["region_sigungu"],
        "board_name": board["name"],
        "board_url": board["board_url"],
        "title": title,
        "notice_no": notice_no,
        "posted_at": posted_at,
        "dept": dept,
        "detail_url": detail_url,
        "body_text": "",
        "attachments": [],
        "matched_keywords": matched,
        "excluded_reason": excluded_reason(title, matched),
        "is_result_notice": bool(RESULT_RE.search(title)),
        "_key": key,
    }


# ---------------------------------------------------------------------------
# 새올 전자민원 (중구·남구·동구·북구)
# ---------------------------------------------------------------------------
SAEOL_ACTION = "/emwp/gov/mogaha/ntis/web/ofr/action/OfrAction.do"


def saeol_list_params(codes: str, page: int, page_size: int) -> dict:
    # 각 구청 홈페이지 iframe(OfrNotAncmtLSub.jsp)의 form1 hidden 값과 같다.
    return {
        "epcCheck": "", "pageIndex": str(page), "jndinm": "OfrNotAncmtEJB", "context": "NTIS",
        "method": "selectListOfrNotAncmt", "methodnm": "selectListOfrNotAncmtHomepage",
        "not_ancmt_mgt_no": "", "homepage_pbs_yn": "Y", "subCheck": "Y",
        "ofr_pageSize": str(page_size), "not_ancmt_se_code": codes, "title": "",
        "cha_dep_code_nm": "", "initValue": "", "countYn": "Y", "list_gubun": "A",
        "not_ancmt_sj": "", "not_ancmt_cn": "", "dept_nm": "", "cgg_code": "",
        "yyyy": "", "yyyymmdd": "", "recent_mm": "", "last_mm": "",
        "nodate_recent_mm": "", "nodate_last_mm": "", "not_ancmt_reg_no": "",
        "Key": "B_Subject", "temp": "",
    }


def saeol_detail_url(host: str, mgt_no: str, codes: str) -> str:
    q = {
        "not_ancmt_mgt_no": mgt_no, "subCheck": "Y", "not_ancmt_se_code": codes,
        "method": "selectOfrNotAncmt", "methodnm": "selectOfrNotAncmtRegst",
        "jndinm": "OfrNotAncmtEJB", "context": "NTIS", "list_gubun": "A", "homepage_pbs_yn": "Y",
    }
    return f"https://{host}{SAEOL_ACTION}?{up.urlencode(q)}"


DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NOTICE_NO = re.compile(r"제\s*\d{4}\s*-\s*\d+\s*호")


def parse_saeol_list(html, org, board):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    # 행은 보통 표(tr·td)지만 광주 서·남·동구처럼 목록(ul·li)인 곳도 있음.
    # 상세 번호는 searchDetail('번호') 또는 광산구처럼 링크의 not_ancmt_mgt_no=번호
    for row in soup.find_all(["tr", "ul"]):
        m = re.search(r"searchDetail\('(\d+)'\)|not_ancmt_mgt_no=(\d+)", str(row))
        tds = row.find_all("td" if row.name == "tr" else "li", recursive=False)
        if not m or len(tds) < 4:
            continue
        mgt_no = m.group(1) or m.group(2)
        texts = [clean(td.get_text(" ")) for td in tds]
        date_idx = next((i for i, t in enumerate(texts) if DATE_ONLY.match(t)), None)
        notice_no = next((t for t in texts if NOTICE_NO.search(t)), "")
        # 제목 칸: 날짜·번호·공고번호·게재기간이 아닌 칸 중 가장 긴 것
        cands = [
            (i, t) for i, t in enumerate(texts)
            if t and t != notice_no and not t.isdigit() and not DATE_ONLY.match(t) and "~" not in t
        ]
        if not cands:
            continue
        ti, title_raw = max(cands, key=lambda x: len(x[1]))
        dept = texts[date_idx - 1] if date_idx and date_idx - 1 != ti else ""
        title, truncated = strip_ellipsis(title_raw)
        rec = make_record(
            org, board, title=title, notice_no=notice_no,
            posted_at=texts[date_idx] if date_idx is not None else "", dept=dept,
            detail_url=saeol_detail_url(org["host"], mgt_no, board["codes"]),
            key=mgt_no,
        )
        rec["_truncated"] = truncated
        out.append(rec)
    return out


def _labeled(lines, label):
    """'라벨\n값' 또는 '라벨 : 값' 형식에서 값을 꺼낸다."""
    for i, line in enumerate(lines):
        if line.replace(" ", "").startswith(label.replace(" ", "")):
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if rest:
                return rest
            if i + 1 < len(lines):
                return lines[i + 1].lstrip(": ").strip()
    return ""


def saeol_download_base(html: str) -> tuple[str, bool]:
    """상세 페이지의 goDownLoad() 구현에서 다운로드 JSP 경로를 찾는다.

    중구: <form name="nnn" action="/emwp/jsp/ofr/FileDownNew.jsp"> POST (GET도 동작 확인)
    남구·동구·북구: location.href="/emwp/jsp/ofr/FileDown(New).jsp?..." GET
    """
    m = re.search(r'<form[^>]*name\s*=\s*"nnn"[^>]*action\s*=\s*"([^"]+)"', html)
    if m:
        return m.group(1), True
    code = "\n".join(l for l in html.splitlines() if not l.strip().startswith("//"))
    m = re.search(r'location\.href\s*=\s*"([^"?]*FileDown\w*\.jsp)', code)
    return (m.group(1) if m else "/emwp/jsp/ofr/FileDown.jsp"), False


def parse_saeol_detail(html, rec, host):
    soup = BeautifulSoup(html, "html.parser")
    base, is_home = saeol_download_base(html)
    atts = []
    for a in soup.find_all("a"):
        js = (a.get("href") or "") + " " + (a.get("onclick") or "")
        m = re.search(r"goDownLoad\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'", js)
        if not m:
            continue
        q = {"user_file_nm": m.group(1), "sys_file_nm": m.group(2), "file_path": m.group(3)}
        if is_home:
            q["isHome"] = "Y"
        atts.append({
            "name": clean(a.get_text()) or m.group(1),
            # 중구는 form POST 방식(+ 인코딩, GET도 동작 확인), 나머지는 encodeURI 방식
            "url": f"https://{host}{base}?" + (up.urlencode(q) if is_home else encode_uri_query(q)),
        })
    text = page_text(soup)
    lines = text.split("\n")
    if not rec["notice_no"]:
        rec["notice_no"] = _labeled(lines, "고시공고번호")
    if not rec["dept"]:
        rec["dept"] = _labeled(lines, "담당부서")
    if not rec["posted_at"]:
        rec["posted_at"] = norm_date(_labeled(lines, "등록일"))
    full = _labeled(lines, "제목")
    if rec.get("_truncated"):
        prefix = rec["title"]
        hit = full if full.startswith(prefix) else next((l for l in lines if l.startswith(prefix)), "")
        if hit:
            rec["title"] = hit
    rec["body_text"] = text[:BODY_LIMIT]
    rec["attachments"] = atts


def crawl_saeol(f, org, board, pages, page_size):
    url = f"https://{org['host']}{SAEOL_ACTION}"
    recs = []
    for p in range(1, pages + 1):
        page_size = board.get("page_size", page_size)
        html = f.post(url, saeol_list_params(board["codes"], p, page_size))
        got = parse_saeol_list(html, org, board)
        recs += got
        if len(got) < page_size:
            break
    return recs


# ---------------------------------------------------------------------------
# 울주군 홈페이지 (새올 연동 게시판, 제목 전체가 title 속성에 있음)
# ---------------------------------------------------------------------------
ULJU = "https://www.ulju.ulsan.kr"


def crawl_ulju(f, org, board, pages, page_size):
    recs = []
    for p in range(1, pages + 1):
        if p == 1:
            html = f.get(board["list_url"])
        else:
            html = f.post(board["list_url"], {"page": str(p), "seCode": board["se_code"],
                                              "searchType": "tit", "searchTxt": ""})
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("table.bod_list tbody tr")
        for tr in rows:
            tds = tr.find_all("td", recursive=False)
            a = tr.find("a", attrs={"data-action": True})
            if len(tds) < 5 or not a:
                continue
            title = clean(a.get("title") or a.get_text())
            detail = up.urljoin(ULJU, a["data-action"])
            key = up.parse_qs(up.urlparse(detail).query).get("notAncmtMgtNo", [detail])[0]
            recs.append(make_record(
                org, board, title=title, notice_no=clean(tds[1].get_text(" ")),
                posted_at=norm_date(tds[4].get_text()), dept=clean(tds[3].get_text()),
                detail_url=detail, key=key,
            ))
        if len(rows) < 10:
            break
    return recs


def parse_ulju_detail(html, rec, _host):
    soup = BeautifulSoup(html, "html.parser")
    atts = []
    for a in soup.select("dl.view_file a, .view_file a"):
        m = re.search(r"goDownload_location\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'",
                      a.get("onclick") or "")
        if not m:
            continue
        # 받을 때 레거시 TLS + Referer(상세 페이지 URL) 필요. README 참고.
        q = encode_uri_query({"user_file_nm": m.group(1), "sys_file_nm": m.group(2),
                              "file_path": m.group(3)})
        atts.append({"name": clean(a.get_text()) or m.group(1),
                     "url": f"https://eminwon.ulju.ulsan.kr/emwp/jsp/ofr/FileDown.jsp?{q}"})
    view = soup.select_one("div.bod_view") or soup.select_one("#conts") or soup
    info = soup.select_one("div.view_info")
    body = soup.select_one("div.view_cont")
    head = page_text(info) if info else ""
    main = page_text(body) if body else page_text(view)
    rec["body_text"] = (head + "\n" + main).strip()[:BODY_LIMIT]
    rec["attachments"] = atts


# ---------------------------------------------------------------------------
# 울산광역시청 고시공고 (자체 CMS)
# ---------------------------------------------------------------------------
ULSAN = "https://www.ulsan.go.kr"
ULSAN_LIST = "/u/rep/transfer/notice/list.ulsan"
ULSAN_MID = "001004002000000000"


def crawl_ulsan(f, org, board, pages, page_size):
    recs = []
    for p in range(1, pages + 1):
        html = f.get(f"{ULSAN}{ULSAN_LIST}?" + up.urlencode({"mId": ULSAN_MID, "curPage": p}))
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("table.tbl_bd_list tbody tr")
        for tr in rows:
            tds = tr.find_all("td", recursive=False)
            a = tr.find("a", href=True)
            if len(tds) < 6 or not a:
                continue
            detail = up.urljoin(f"{ULSAN}{ULSAN_LIST}", a["href"])
            key = re.search(r"/(\d+)\.ulsan", detail)
            recs.append(make_record(
                org, board, title=clean(a.get_text()), notice_no=clean(tds[4].get_text()),
                posted_at=norm_date(tds[5].get_text()), dept=clean(tds[2].get_text()),
                detail_url=detail, key=key.group(1) if key else detail,
            ))
        if not rows:
            raise RuntimeError("목록 표를 찾지 못함 (대기열 TRACER 페이지 또는 구조 변경 가능)")
        if len(rows) < 10:
            break
    return recs


def parse_ulsan_detail(html, rec, _host):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.tbl_bd_view")
    if not table:
        rec["body_text"] = page_text(soup.select_one(".content_box") or soup)[:BODY_LIMIT]
        return
    atts = []
    for a in table.find_all("a", href=True):
        if "download" in a["href"]:
            atts.append({"name": clean(a.get_text()), "url": up.urljoin(ULSAN, a["href"])})
    fields, body = [], ""
    for th in table.find_all("th"):
        td = th.find_next_sibling("td")
        if not td:
            continue
        label = clean(th.get_text())
        if label == "첨부파일":
            continue
        if label == "내용":
            body = page_text(td)
        else:
            fields.append(f"{label}: {clean(td.get_text(' '))}")
    rec["body_text"] = ("\n".join(fields) + "\n\n" + body).strip()[:BODY_LIMIT]
    rec["attachments"] = atts


# ---------------------------------------------------------------------------
# 첨부 본문 추출 (--attach, --attach-only)
# ---------------------------------------------------------------------------
ATTACH_LIMIT = 6000          # attachment_text 최대 글자 수
ATTACH_MAX_FILES = 2         # 공고당 내려받는 첨부 수
ATTACH_MAX_BYTES = 30 * 1024 * 1024
ATTACH_XML_MAX = 50 * 1024 * 1024   # hwpx 안 section XML 압축 해제 상한 (zip bomb 방지)
# 첨부 다운로드에 레거시 TLS가 필요한 호스트 (동구 새올: 옛 암호군, 울주 새올: 짧은 DH 키)
ATTACH_LEGACY_HOSTS = ["eminwon.donggu.ulsan.kr", "eminwon.ulju.ulsan.kr", "eminwon.suwon.go.kr", "eminwon.chuncheon.go.kr"]
# 합격자 명단 같은 결과 문서는 개인정보가 들어 있으므로 받지 않는다
ATTACH_RESULT_RE = re.compile(RESULT_RE.pattern + r"|합격자|명단")
# 응시원서·서식은 공고문보다 뒤로 미룬다
ATTACH_FORM_RE = re.compile(r"서식|원서|신청서|양식|기준표|동의서|이력서|자기소개서|서약서")

try:  # pdf 추출은 pypdf가 있을 때만
    import pypdf  # type: ignore
except ImportError:  # pragma: no cover
    pypdf = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def hwpx_text(data: bytes) -> str:
    """hwpx(zip) 안 Contents/section*.xml 의 hp:t 텍스트를 문단(hp:p) 단위 줄로 모은다."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET

    zf = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in zf.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)]
    names.sort(key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
    if not names:
        raise ValueError("hwpx 안에 Contents/section*.xml 없음")
    lines: list[str] = []
    cur: list[str] = []

    def flush():
        line = clean("".join(cur))
        if line:
            lines.append(line)
        cur.clear()

    def walk(e):
        tag = _local(e.tag)
        if tag == "p":  # 문단(표 안 셀 문단 포함)마다 줄을 나눈다
            flush()
            for c in e:
                walk(c)
            flush()
        elif tag == "t":
            cur.append(e.text or "")
            for c in e:  # <hp:tab/>, <hp:lineBreak/> 뒤에 글자가 tail로 붙는다
                ct = _local(c.tag)
                cur.append("\n" if ct == "lineBreak" else " " if ct in ("tab", "fwSpace", "nbSpace") else "")
                cur.append(c.tail or "")
        else:
            for c in e:
                walk(c)

    for n in names:
        if zf.getinfo(n).file_size > ATTACH_XML_MAX:
            raise ValueError(f"{n} 압축 해제 크기 초과")
        walk(ET.fromstring(zf.read(n)))
        flush()
    return "\n".join(lines)


def pdf_text(data: bytes) -> str:
    import io
    reader = pypdf.PdfReader(io.BytesIO(data))
    out, size = [], 0
    for page in reader.pages:
        t = page.extract_text() or ""
        out.append(t)
        size += len(t)
        if size > ATTACH_LIMIT * 2:
            break
    lines = [clean(x) for x in "\n".join(out).split("\n")]
    return "\n".join(x for x in lines if x)


def _ext(name: str) -> str:
    m = re.search(r"\.([A-Za-z0-9]+)\s*$", name or "")
    return m.group(1).lower() if m else ""


def _stem(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]+\s*$", "", name or "").strip()


def attach_target(n: dict) -> bool:
    """첨부를 받을 공고: 제외·결과 공고가 아니고 첨부가 있는 것."""
    return bool(n.get("attachments")) and not n.get("excluded_reason") and not n.get("is_result_notice")


def plan_attachments(atts: list[dict]) -> tuple[list[dict], list[dict]]:
    """받을 후보(우선순위 순)와 건너뛸 첨부(사유 포함)로 나눈다."""
    cands, skipped = [], []
    for a in atts:
        name, ext = a.get("name", ""), _ext(a.get("name", ""))
        if ATTACH_RESULT_RE.search(name):
            skipped.append({"name": name, "status": "skipped", "note": "결과·합격자 문서 (개인정보, 받지 않음)"})
        elif ext not in ("hwpx", "pdf"):
            skipped.append({"name": name, "status": "skipped", "note": f"지원하지 않는 형식 (.{ext or '?'})"})
        elif ext == "pdf" and pypdf is None:
            skipped.append({"name": name, "status": "skipped", "note": "pypdf 없음"})
        else:
            cands.append(a)
    # 공고문 > 기타 > 서식·원서, 같은 순위면 hwpx 먼저
    cands.sort(key=lambda a: (0 if "공고" in a["name"] and not ATTACH_FORM_RE.search(a["name"])
                              else 2 if ATTACH_FORM_RE.search(a["name"]) else 1,
                              0 if _ext(a["name"]) == "hwpx" else 1))
    return cands, skipped


def fetch_attachment_text(f: "Fetcher", n: dict) -> None:
    """공고 하나의 첨부를 최대 ATTACH_MAX_FILES개 받아 attachment_text / attachment_files 를 채운다."""
    cands, files = plan_attachments(n.get("attachments") or [])
    texts, tried, ok_stems = [], 0, set()
    for a in cands:
        name, ext = a["name"], _ext(a["name"])
        if ext == "pdf" and _stem(name) in ok_stems:
            files.append({"name": name, "status": "skipped", "note": "같은 이름의 hwpx를 이미 추출"})
            continue
        if tried >= ATTACH_MAX_FILES:
            files.append({"name": name, "status": "skipped", "note": f"공고당 최대 {ATTACH_MAX_FILES}개"})
            continue
        tried += 1
        try:
            data = f.get_bytes(a["url"], referer=n.get("detail_url"), max_bytes=ATTACH_MAX_BYTES)
            if ext == "hwpx":
                if not data.startswith(b"PK"):
                    raise ValueError(f"zip 형식이 아님 (응답 앞부분: {data[:40]!r})")
                text = hwpx_text(data)
            else:
                if not data.startswith(b"%PDF"):
                    raise ValueError(f"PDF 형식이 아님 (응답 앞부분: {data[:40]!r})")
                text = pdf_text(data)
            if not text.strip():
                raise ValueError("추출한 텍스트가 비어 있음 (스캔 이미지 PDF일 수 있음)")
            ok_stems.add(_stem(name))
            texts.append((name, text))
            files.append({"name": name, "status": "ok", "bytes": len(data), "chars": len(text)})
        except Exception as e:
            files.append({"name": name, "status": "error", "note": f"{type(e).__name__}: {e}"[:300]})
    if len(texts) == 1:
        joined = texts[0][1]
    else:
        joined = "\n\n".join(f"[{name}]\n{text}" for name, text in texts)
    n["attachment_text"] = joined[:ATTACH_LIMIT]
    n["attachment_files"] = files


def run_attachments(notices: list[dict], verify: bool, *, reuse: dict | None = None,
                    only_missing: bool = False) -> dict:
    """대상 공고의 첨부를 받아 텍스트를 넣는다.

    reuse: detail_url → 이전 실행의 공고. 첨부 URL이 같고 성공한 적이 있으면 다시 받지 않는다.
    only_missing: 이미 attachment_text 가 있고 오류가 없던 공고는 건너뛴다 (--attach-only).
    """
    f = Fetcher(legacy_hosts=ATTACH_LEGACY_HOSTS, verify=verify)
    st = {"targets": 0, "reused": 0, "notices_ok": 0, "notices_partial": 0, "notices_failed": 0,
          "notices_skipped": 0, "files_ok": 0, "files_error": 0, "files_skipped": 0, "requests": 0}
    for n in notices:
        if not attach_target(n):
            continue
        st["targets"] += 1
        prev = (reuse or {}).get(n.get("detail_url"))
        if prev and prev.get("attachment_text") and \
                [a["url"] for a in prev.get("attachments", [])] == [a["url"] for a in n["attachments"]] and \
                not any(x.get("status") == "error" for x in prev.get("attachment_files", [])):
            n["attachment_text"] = prev["attachment_text"]
            n["attachment_files"] = prev.get("attachment_files", [])
            st["reused"] += 1
        elif only_missing and n.get("attachment_text") and \
                not any(x.get("status") == "error" for x in n.get("attachment_files", [])):
            st["reused"] += 1
        else:
            fetch_attachment_text(f, n)
            ok = [x for x in n["attachment_files"] if x["status"] == "ok"]
            err = [x for x in n["attachment_files"] if x["status"] == "error"]
            label = ("성공" if ok and not err else "일부 실패" if ok else "실패" if err
                     else "받을 첨부 없음")
            print(f"  [첨부] {n['region_sigungu']} {n['title'][:40]}: {label} "
                  f"({', '.join(x['name'][:30] + ' ' + x['status'] for x in n['attachment_files'])})")
        files = n.get("attachment_files", [])
        ok = sum(1 for x in files if x["status"] == "ok")
        err = sum(1 for x in files if x["status"] == "error")
        st["files_ok"] += ok
        st["files_error"] += err
        st["files_skipped"] += sum(1 for x in files if x["status"] == "skipped")
        if ok and not err:
            st["notices_ok"] += 1
        elif ok:
            st["notices_partial"] += 1
        elif err:
            st["notices_failed"] += 1
        else:  # 받을 만한 첨부가 없음 (결과 문서·hwp 등만 있음)
            st["notices_skipped"] += 1
    st["requests"] = f.count
    return st


def print_attach_summary(st: dict) -> None:
    print(f"\n첨부: 대상 {st['targets']}건 (재사용 {st['reused']}) / 성공 {st['notices_ok']}, "
          f"일부 {st['notices_partial']}, 실패 {st['notices_failed']}, 받을 첨부 없음 {st['notices_skipped']} "
          f"/ 파일 성공 {st['files_ok']}, 오류 {st['files_error']}, 건너뜀 {st['files_skipped']}, "
          f"요청 {st['requests']}회")


# ---------------------------------------------------------------------------
# 기관 설정
# ---------------------------------------------------------------------------
def saeol_org(key, name, region, host, gosi_codes, home_url, legacy=False):
    base = f"https://{host}{SAEOL_ACTION}"
    return {
        "key": key, "source_org": name, "region_sigungu": region, "host": host,
        "kind": "saeol", "legacy_tls": legacy,
        "boards": [
            # 05 = 채용공고. 구청 홈페이지 '채용공고' 메뉴가 같은 새올 데이터를 보여준다.
            {"name": "새올 채용공고", "codes": "05",
             "board_url": f"{base}?not_ancmt_se_code=05"},
            # 홈페이지 고시/공고 iframe 이 쓰는 구분코드 그대로
            {"name": "새올 고시공고", "codes": gosi_codes,
             "board_url": home_url},
        ],
    }


def saeol_jobs_org(key, name, region, host, legacy=False):
    """다른 시·도 대표 지역: 새올 채용공고(05) 게시판만 (부하·실행 시간을 줄이려고 고시공고는 보지 않음)"""
    o = saeol_org(key, name, region, host, "", "", legacy)
    o["boards"] = o["boards"][:1]
    return o


ORGS = [
    saeol_org("중구", "울산광역시 중구청", "울산광역시 중구", "eminwon.junggu.ulsan.kr", "01,02,04",
              "https://www.junggu.ulsan.kr/index.ulsan?menuCd=DOM_000000102004001000"),
    saeol_org("남구", "울산광역시 남구청", "울산광역시 남구", "eminwon.ulsannamgu.go.kr", "01,04",
              "https://www.ulsannamgu.go.kr/cop/bbs/selectSaeolGosiList.do"),
    saeol_org("동구", "울산광역시 동구청", "울산광역시 동구", "eminwon.donggu.ulsan.kr", "01,03,04,05",
              "https://www.donggu.ulsan.kr/donggu/dongguNews/gosi/contents.do", legacy=True),
    saeol_org("북구", "울산광역시 북구청", "울산광역시 북구", "eminwon.bukgu.ulsan.kr", "01,03,04",
              "https://www.bukgu.ulsan.kr/lay1/S1T86C456/contents.do"),
    {
        "key": "울주군", "source_org": "울산광역시 울주군청", "region_sigungu": "울산광역시 울주군",
        "host": "www.ulju.ulsan.kr", "kind": "ulju", "legacy_tls": False,
        "boards": [
            {"name": "울주군 채용공고", "se_code": "05",
             "list_url": f"{ULJU}/ulju/unit/employ/list.do?mId=0403030100",
             "board_url": f"{ULJU}/ulju/contents.do?mId=0403030000"},
            {"name": "울주군 일반공고", "se_code": "04",
             "list_url": f"{ULJU}/ulju/saeol/gosi/list.do?seCode=04&mId=0403020000",
             "board_url": f"{ULJU}/ulju/contents.do?mId=0403020000"},
        ],
    },
    {
        "key": "울산시", "source_org": "울산광역시청", "region_sigungu": "울산광역시",
        "host": "www.ulsan.go.kr", "kind": "ulsan", "legacy_tls": False,
        "boards": [
            {"name": "울산시 고시공고",
             "board_url": f"{ULSAN}{ULSAN_LIST}?mId={ULSAN_MID}"},
        ],
    },
]
# 1단계 지역 확장 (2026-10-16): 시·도마다 대표 1곳, 새올 채용 게시판 목록이 열리는 것을 확인한 곳
ORGS += [
    saeol_jobs_org("해운대구", "부산광역시 해운대구청", "부산광역시 해운대구", "eminwon.haeundae.go.kr"),
    saeol_jobs_org("광주북구", "광주광역시 북구청", "광주광역시 북구", "eminwon.bukgu.gwangju.kr"),
    saeol_jobs_org("대전서구", "대전광역시 서구청", "대전광역시 서구", "eminwon.seogu.go.kr"),
    saeol_jobs_org("세종시", "세종특별자치시청", "세종특별자치시 세종시", "eminwon.sejong.go.kr"),
    saeol_jobs_org("수원시", "경기도 수원시청", "경기도 수원시", "eminwon.suwon.go.kr", legacy=True),
    saeol_jobs_org("춘천시", "강원특별자치도 춘천시청", "강원특별자치도 춘천시", "eminwon.chuncheon.go.kr", legacy=True),
]
ORGS_DIR = Path(__file__).resolve().parent.parent / "orgs"


def load_org_file(path):
    """전국 조사 결과(orgs/<시·도>.json)에서 status ok 이고 기본 목록(ORGS)에 없는 기관 → 새올 채용 게시판만 수집"""
    have = {o["host"] for o in ORGS}
    out = []
    for e in json.loads(Path(path).read_text(encoding="utf-8")):
        if e.get("status") != "ok" or e.get("note") == "기존" or e["host"] in have:
            continue
        legacy = bool(e.get("legacy"))
        if e.get("web"):
            # 새올을 쓰지 않는 누리집 게시판 (scripts/web_board.py)
            board = {"name": "누리집 게시판", "board_url": e["web"].get("board_url") or e["web"]["list_url"], "web": e["web"]}
            WEB_BOARDS[e["name"]] = board
            out.append({"key": e["name"], "source_org": e["name"], "region_sigungu": f'{e["sido"]} {e["sigungu"]}',
                        "host": up.urlparse(e["web"]["list_url"]).hostname, "kind": "web", "legacy_tls": legacy,
                        "boards": [board]})
            continue
        o = saeol_jobs_org(e["name"], e["name"], f'{e["sido"]} {e["sigungu"]}', e["host"], legacy)
        if e.get("codes"):
            # 채용(05) 게시판을 안 쓰고 고시/공고에 채용 공고를 섞어 올리는 기관: 그 구분으로 30건씩 보고 키워드로 거름
            b = o["boards"][0]
            b.update(name="새올 고시공고", codes=e["codes"], page_size=30,
                     board_url=b["board_url"].replace("not_ancmt_se_code=05", "not_ancmt_se_code=" + e["codes"]))
        out.append(o)
        if legacy and e["host"] not in ATTACH_LEGACY_HOSTS:
            ATTACH_LEGACY_HOSTS.append(e["host"])
    return out


WEB_BOARDS: dict[str, dict] = {}  # 기관명 → 누리집 게시판 설정 (상세 파싱 때 씀)


def crawl_web(f, org, board, pages, page_size):
    return web_board.crawl_web(f, org, board, pages, page_size, make_record=make_record, strip_ellipsis=strip_ellipsis)


def parse_web_detail(html, rec, host):
    web_board.parse_web_detail(html, rec, host, board=WEB_BOARDS[rec["source_org"]], page_text=page_text, clean=clean)


ALIASES = {"울산시청": "울산시", "울산광역시": "울산시", "시청": "울산시", "울주": "울주군"}

LIST_FN = {"saeol": crawl_saeol, "ulju": crawl_ulju, "ulsan": crawl_ulsan, "web": crawl_web}
DETAIL_FN = {"saeol": parse_saeol_detail, "ulju": parse_ulju_detail, "ulsan": parse_ulsan_detail, "web": parse_web_detail}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def run_org(org, pages, page_size, with_detail, verify):
    f = Fetcher(legacy_hosts=[org["host"]] if org["legacy_tls"] else [], verify=verify)
    log = {"org": org["key"], "source_org": org["source_org"], "boards": [], "ok": True}
    seen, kept = set(), []
    for board in org["boards"]:
        b = {"board": board["name"], "listed": 0, "matched": 0, "excluded": 0, "error": None}
        try:
            recs = LIST_FN[org["kind"]](f, org, board, pages, page_size)
        except Exception as e:  # 기관 하나가 실패해도 나머지는 계속한다
            b["error"] = f"{type(e).__name__}: {e}"[:300]
            log["ok"] = False
            log["boards"].append(b)
            print(f"  [{org['key']}] {board['name']} 목록 실패: {b['error']}")
            continue
        b["listed"] = len(recs)
        for r in recs:
            if r["_key"] in seen:
                continue
            seen.add(r["_key"])
            if not r["matched_keywords"]:
                continue
            b["matched"] += 1
            kept.append(r)
        log["boards"].append(b)
        print(f"  [{org['key']}] {board['name']}: 목록 {b['listed']}건, 키워드 통과 {b['matched']}건")

    detail_ok = detail_fail = 0
    for r in kept:
        if not with_detail or r["excluded_reason"] or RESULT_RE.search(r["title"]):
            continue  # 제외 건·결과(합격자) 공고는 상세를 요청하지 않는다 (개인정보)
        try:
            html = f.get(r["detail_url"])
            DETAIL_FN[org["kind"]](html, r, org["host"])
            # 상세에서 얻은 전체 제목으로 키워드·제외 판정을 다시 한다
            r["matched_keywords"] = match_keywords(r["title"])
            r["excluded_reason"] = excluded_reason(r["title"], r["matched_keywords"])
            r["is_result_notice"] = bool(RESULT_RE.search(r["title"]))
            if r["is_result_notice"]:
                r["body_text"] = ""  # 전체 제목으로 결과 공고가 드러나면 본문을 남기지 않음
            detail_ok += 1
        except Exception as e:
            r["detail_error"] = f"{type(e).__name__}: {e}"[:300]
            detail_fail += 1
    for b in log["boards"]:
        names = {r["_key"] for r in kept if r["board_name"] == b["board"] and r["excluded_reason"]}
        b["excluded"] = len(names)
    log.update(requests=f.count, detail_ok=detail_ok, detail_fail=detail_fail,
               kept=len(kept), excluded=sum(1 for r in kept if r["excluded_reason"]))
    for r in kept:
        r.pop("_key", None)
        r.pop("_truncated", None)
    return kept, log


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="울산 시·구·군 공공일자리 공고 크롤러 (raw 수집)")
    ap.add_argument("--orgs", default="", help="쉼표 구분: 중구,남구,동구,북구,울주군,울산시 (기본 전체)")
    ap.add_argument("--org-file", default="",
                    help="기본 목록 대신 전국 조사 결과 파일(orgs/<시·도>.json)의 기관을 수집")
    ap.add_argument("--pages", type=int, default=1, help="게시판별 목록 쪽수 (1~2 권장, 최대 3)")
    ap.add_argument("--page-size", type=int, default=10,
                    help="새올 목록 한 쪽 건수 (ofr_pageSize, 기본 10 = 홈페이지와 동일)")
    ap.add_argument("--no-detail", action="store_true", help="상세 페이지를 요청하지 않음")
    ap.add_argument("--insecure", action="store_true", help="TLS 인증서 검증 끄기 (진단용)")
    ap.add_argument("--out", default=str(OUT_DIR / "raw_notices.json"))
    ap.add_argument("--merge", action="store_true",
                    help="--out 기존 파일에서 이번에 돌리지 않은 기관의 결과는 유지 (한 기관만 재수집할 때)")
    ap.add_argument("--refilter", action="store_true",
                    help="네트워크 요청 없이 --out 파일에 키워드·제외 규칙만 다시 적용")
    ap.add_argument("--attach", action="store_true",
                    help="수집 후 대상 공고(제외·결과 공고 아님)의 .hwpx/.pdf 첨부를 받아 attachment_text 저장")
    ap.add_argument("--attach-only", action="store_true",
                    help="목록·상세 수집 없이 --out 파일의 공고에 첨부 텍스트만 채움 (--orgs로 기관 한정 가능)")
    args = ap.parse_args(argv)
    if args.attach_only and args.refilter:
        ap.error("--attach-only 와 --refilter 는 함께 쓸 수 없습니다 (차례로 실행하세요)")
    if args.attach and args.no_detail:
        ap.error("--attach 는 상세 페이지의 첨부 목록이 필요하므로 --no-detail 과 함께 쓸 수 없습니다")

    if args.attach_only:
        path = Path(args.out)
        notices = json.loads(path.read_text(encoding="utf-8"))
        wanted = [ALIASES.get(x.strip(), x.strip()) for x in args.orgs.split(",") if x.strip()]
        orgs = {o["source_org"] for o in ORGS if not wanted or o["key"] in wanted}
        started = datetime.now(KST)
        st = run_attachments([n for n in notices if n["source_org"] in orgs], not args.insecure,
                             only_missing=True)
        path.write_text(json.dumps(notices, ensure_ascii=False, indent=2), encoding="utf-8")
        log_path = path.parent / "crawl_log.json"
        log_doc = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {}
        log_doc["attach"] = {"started_at": started.isoformat(timespec="seconds"),
                             "finished_at": datetime.now(KST).isoformat(timespec="seconds"),
                             "mode": "attach-only", **st}
        log_path.write_text(json.dumps(log_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print_attach_summary(st)
        print(f"저장: {path}")
        return 0 if not st["notices_failed"] else 1

    if args.refilter:
        path = Path(args.out)
        notices = json.loads(path.read_text(encoding="utf-8"))
        for n in notices:
            n["matched_keywords"] = match_keywords(n["title"])
            n["excluded_reason"] = excluded_reason(n["title"], n["matched_keywords"])
            n["is_result_notice"] = bool(RESULT_RE.search(n["title"]))
        notices = [n for n in notices if n["matched_keywords"]]
        path.write_text(json.dumps(notices, ensure_ascii=False, indent=2), encoding="utf-8")
        ex = sum(1 for n in notices if n["excluded_reason"])
        print(f"재판정: {path} {len(notices)}건 (제외 {ex}건)")
        return 0

    pages = max(1, min(args.pages, 3))
    page_size = max(5, min(args.page_size, 30))
    base = load_org_file(args.org_file) if args.org_file else ORGS
    wanted = [ALIASES.get(x.strip(), x.strip()) for x in args.orgs.split(",") if x.strip()]
    orgs = [o for o in base if not wanted or o["key"] in wanted]
    unknown = set(wanted) - {o["key"] for o in base}
    if unknown:
        ap.error(f"알 수 없는 기관: {', '.join(sorted(unknown))}")
    if args.insecure:
        import urllib3
        urllib3.disable_warnings()

    started = datetime.now(KST)
    notices, logs = [], []
    for org in orgs:
        print(f"[{org['key']}] {org['source_org']} 수집 시작")
        kept, log = run_org(org, pages, page_size, not args.no_detail, not args.insecure)
        notices += kept
        logs.append(log)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    attach_st = None
    if args.attach:
        # 이전 실행에서 이미 받은 첨부(같은 상세 URL·같은 첨부 URL)는 다시 받지 않는다
        prev_all = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
        reuse = {n.get("detail_url"): n for n in prev_all if n.get("attachment_text")}
        print("\n[첨부] 대상 공고의 첨부 내려받기")
        attach_st = run_attachments(notices, not args.insecure, reuse=reuse)
    if args.merge and out.exists():
        ran = {o["source_org"] for o in orgs}
        prev = [n for n in json.loads(out.read_text(encoding="utf-8")) if n["source_org"] not in ran]
        print(f"병합: 기존 파일에서 다른 기관 {len(prev)}건 유지")
        notices = prev + notices
        order = {o["source_org"]: i for i, o in enumerate(ORGS)}
        notices.sort(key=lambda n: order.get(n["source_org"], 99))
    out.write_text(json.dumps(notices, ensure_ascii=False, indent=2), encoding="utf-8")
    log_doc = {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(KST).isoformat(timespec="seconds"),
        "options": {"orgs": [o["key"] for o in orgs], "pages": pages, "page_size": page_size,
                    "detail": not args.no_detail},
        "total_kept": len(notices),
        "total_excluded": sum(1 for n in notices if n["excluded_reason"]),
        "orgs": logs,
    }
    if attach_st is not None:
        log_doc["options"]["attach"] = True
        log_doc["attach"] = attach_st
    log_path = out.parent / "crawl_log.json"
    if args.merge and log_path.exists():
        ran = {lg["org"] for lg in logs}
        old_logs = json.loads(log_path.read_text(encoding="utf-8")).get("orgs", [])
        log_doc["orgs"] = [lg for lg in old_logs if lg["org"] not in ran] + logs
    log_path.write_text(
        json.dumps(log_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n기관       요청  키워드통과  제외  상세성공/실패  상태")
    for lg in logs:
        status = "OK" if lg["ok"] and not lg["detail_fail"] else "일부 실패"
        if not any(b["listed"] for b in lg["boards"]):
            status = "실패"
        print(f"{lg['org']:<8} {lg['requests']:>5} {lg['kept']:>10} {lg['excluded']:>5}"
              f"   {lg['detail_ok']:>4}/{lg['detail_fail']:<4}    {status}")
    if attach_st is not None:
        print_attach_summary(attach_st)
    print(f"\n저장: {out} ({len(notices)}건)")
    return 0 if all(lg["ok"] for lg in logs) else 1


if __name__ == "__main__":
    sys.exit(main())
