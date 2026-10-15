"""새올을 쓰지 않는 기관 누리집 게시판용 범용 수집기 (kind "web")

기관마다 orgs/<시·도>.json 항목에 "web" 설정을 둔다:
  "web": {
    "list_url": "https://www.example.go.kr/board/list.do?bbsId=...",  # 목록 1쪽 (GET)
    "row": "tbody tr",                 # (선택) 행 CSS 선택자. 기본: tbody tr, 없으면 ul > li
    "title": "td.title a",             # (선택) 행 안 제목 링크 선택자. 기본: 행에서 글자가 가장 긴 링크
    "title_skip": "포천|연천",          # (선택) 이 정규식에 걸리는 제목은 뺌 (다른 지역 소식이 섞이는 게시판)
    "date": "td.reg_date",             # (선택) 행 안 등록일 칸 선택자 (마감일 칸과 헷갈리는 게시판). 기본: 행 글자에서 첫 날짜
    "id_re": "fnView\\('(\\d+)'",      # (선택) 링크가 javascript 일 때 행 HTML 에서 글 번호 뽑는 정규식
    "detail_url": "https://.../view.do?nttId={id}",  # (선택) id_re 를 쓸 때 상세 주소 틀
    "body": "div.view_cont",           # (선택) 상세 본문 CSS 선택자. 기본: 자동 추정
    "strip": ".prev_next, .btn_area",  # (선택) 상세 본문에서 지울 부분(이전글·다음글 등) 선택자
    "file_re": "goDownLoad\\('([^']*)','([^']*)','([^']*)'",   # (선택) 첨부가 javascript 일 때
    "file_url": "https://.../FileDown.jsp?user_file_nm={0}&sys_file_nm={1}&file_path={2}",  # {0}{1}.. 또는 {id}(=첫 값)
    "json": {"path": "RSLT_DATA.searchList.dataMap", "id": "col0", "title": "col3", "date": "col5"}
                                       # (선택) 목록이 JSON 으로 오는 게시판: 글 목록 위치와 필드 이름 (상세는 detail_url 틀)
  }
목록에서 제목에 일자리 키워드가 있는 글만 상세를 받는 것은 새올과 같다 (crawler.run_org).
"""
from __future__ import annotations

import json
import re
import urllib.parse as up

from bs4 import BeautifulSoup

DATE_RE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
FILE_EXT = re.compile(r"\.(hwpx|hwp|pdf|docx?|xlsx?|zip)\b", re.I)
FILE_HREF = re.compile(r"download|filedown|file_down|atchfile|attach|fileDn|getFile|downFile", re.I)
VIEWER = re.compile(r"synap|viewer|preview|docview", re.I)  # 첨부 미리보기 링크는 파일이 아님
NOISE = re.compile(r"menu|nav|gnb|lnb|snb|header|footer|quick|banner|location|util|sitemap|skip", re.I)
URI_SAFE = "~@#$&()*!+=:;,.?/'"  # 새올 FileDown.jsp 는 encodeURI 형태만 받음 (crawler.encode_uri_query 와 같음)


def _date(text: str) -> str:
    m = DATE_RE.search(text or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def _title_link(row):
    """행에서 제목 링크: 글자가 가장 긴 <a> (번호·날짜 링크 제외)"""
    best = None
    for a in row.find_all("a"):
        t = re.sub(r"\s+", " ", a.get("title") or "") if len(a.get_text(strip=True)) < 4 else ""
        t = t or re.sub(r"\s+", " ", a.get_text(" ")).strip()
        t = re.sub(r"\s*(새\s*글|새글|new|첨부\s*파일|파일\s*있음)\s*$", "", t, flags=re.I).strip()
        if len(t) >= 6 and not DATE_RE.fullmatch(t) and (best is None or len(t) > len(best[1])):
            best = (a, t)
    return best


def _dig(obj, path):
    for k in path.split("."):
        obj = obj.get(k, {}) if isinstance(obj, dict) else {}
    return obj if isinstance(obj, list) else []


def crawl_web(f, org, board, pages, page_size, *, make_record, strip_ellipsis):
    cfg = board["web"]
    text = f.get(cfg["list_url"])
    out, seen = [], set()

    def add(title_raw, detail, posted):
        if not detail or detail in seen:
            return
        seen.add(detail)
        title, truncated = strip_ellipsis(title_raw)
        rec = make_record(org, board, title=title, notice_no="", posted_at=posted, dept="", detail_url=detail, key=detail)
        rec["_truncated"] = truncated
        out.append(rec)

    if cfg.get("json"):
        j = cfg["json"]
        for it in _dig(json.loads(text), j["path"]):
            add(re.sub(r"\s+", " ", str(it.get(j["title"], ""))).strip(),
                cfg["detail_url"].format(id=it.get(j["id"], "")), _date(str(it.get(j.get("date", ""), ""))))
        return out

    soup = BeautifulSoup(text, "html.parser")
    rows = soup.select(cfg["row"]) if cfg.get("row") else (soup.select("tbody tr") or soup.select("ul > li"))
    for row in rows:
        if cfg.get("title"):
            a = row.select_one(cfg["title"])
            hit = (a, re.sub(r"\s+", " ", a.get("title") or a.get_text(" ")).strip()) if a else None
        else:
            hit = _title_link(row)
        if not hit or not hit[1] or (cfg.get("title_skip") and re.search(cfg["title_skip"], hit[1])):
            continue
        a, title_raw = hit
        href = (a.get("href") or "").strip()
        if cfg.get("id_re"):
            m = re.search(cfg["id_re"], str(row))
            detail = cfg["detail_url"].format(id=m.group(1)) if m else ""
        elif href and not href.lower().startswith(("javascript", "#")):
            detail = up.urljoin(cfg["list_url"], href)
        else:
            detail = ""
        cell = row.select_one(cfg["date"]) if cfg.get("date") else None
        posted = _date(cell.get_text(" ")) if cell else ("" if cfg.get("date") else _date(row.get_text(" ")))
        add(title_raw, detail, posted)
    return out


def _content(soup, cfg):
    if cfg.get("body"):
        node = soup.select_one(cfg["body"])
        if node:
            return node
    for t in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        t.decompose()
    best, size = soup.body or soup, 0
    for el in soup.find_all(["div", "section", "article", "td", "table"]):
        key = " ".join(el.get("class") or []) + " " + (el.get("id") or "")
        if NOISE.search(key):
            continue
        if re.search(r"view|cont|board|bbs|article|detail|read", key, re.I):
            n = len(el.get_text(" ", strip=True))
            if n > size:
                best, size = el, n
    return best


def _file_name(a, groups, clean):
    """첨부 이름: 링크 글자 → title → 다운로드 함수 값 → 감싼 li 글자 순으로 확장자가 있는 것"""
    cands = [clean(a.get_text(" ")), clean(a.get("title"))] + [clean(up.unquote(g or "")) for g in groups]
    if a.parent is not None:
        cands.append(clean(a.parent.get_text(" ")))
    for c in cands:
        m = FILE_EXT.search(c or "")
        if m:
            return re.sub(r"^(hwpx?|pdf|hwp|한글)\s*(문서|파일)\s*", "", c[: m.end()].strip(), flags=re.I)
    return ""


def parse_web_detail(html, rec, host, *, board, page_text, clean):
    cfg = board["web"]
    soup = BeautifulSoup(html, "html.parser")
    atts, seen = [], set()
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        js = href + " " + (a.get("onclick") or "")
        fm = re.search(cfg["file_re"], js) if cfg.get("file_re") else None
        groups = fm.groups() if fm else ()
        name = _file_name(a, groups, clean)
        if not name:
            continue
        if VIEWER.search(js) or (cfg.get("file_skip") and re.search(cfg["file_skip"], js)):
            continue
        url = ""
        if fm:
            vals = [up.quote(g or "", safe=URI_SAFE) for g in groups]
            url = cfg["file_url"].format(*vals, id=vals[0] if vals else "")
        elif href and not href.lower().startswith(("javascript", "#")) and (FILE_HREF.search(href) or FILE_EXT.search(href)):
            url = up.urljoin(rec["detail_url"], href)
        if url and url not in seen:
            seen.add(url)
            atts.append({"name": name, "url": url})
    node = _content(soup, cfg)
    if cfg.get("strip"):
        for el in node.select(cfg["strip"]):
            el.decompose()
    text = page_text(node)
    if rec.get("_truncated"):
        hit = next((l for l in text.split("\n") if l.startswith(rec["title"])), "")
        if hit:
            rec["title"] = hit
    if not rec["posted_at"] and not cfg.get("date"):
        rec["posted_at"] = _date(text[:500])
    rec["body_text"] = text[:3000]
    rec["attachments"] = atts
