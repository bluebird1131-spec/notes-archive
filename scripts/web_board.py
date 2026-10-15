"""새올을 쓰지 않는 기관 누리집 게시판용 범용 수집기 (kind "web")

기관마다 orgs/<시·도>.json 항목에 "web" 설정을 둔다:
  "web": {
    "list_url": "https://www.example.go.kr/board/list.do?bbsId=...",  # 목록 1쪽 (GET)
    "row": "tbody tr",                 # (선택) 행 CSS 선택자. 기본: tbody tr, 없으면 ul > li
    "id_re": "fnView\\('(\\d+)'",      # (선택) 링크가 javascript 일 때 행 HTML 에서 글 번호 뽑는 정규식
    "detail_url": "https://.../view.do?nttId={id}",  # (선택) id_re 를 쓸 때 상세 주소 틀
    "body": "div.view_cont",           # (선택) 상세 본문 CSS 선택자. 기본: 자동 추정
    "file_re": "fnDown\\('([^']+)'",   # (선택) 첨부가 javascript 일 때 파일 키 정규식
    "file_url": "https://.../down.do?key={id}"  # (선택) file_re 를 쓸 때 첨부 주소 틀
  }
목록에서 제목에 일자리 키워드가 있는 글만 상세를 받는 것은 새올과 같다 (crawler.run_org).
"""
from __future__ import annotations

import re
import urllib.parse as up

from bs4 import BeautifulSoup

DATE_RE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
FILE_EXT = re.compile(r"\.(hwpx|hwp|pdf|docx?|xlsx?|zip)\b", re.I)
FILE_HREF = re.compile(r"download|filedown|file_down|atchfile|attach|fileDn|getFile|downFile", re.I)
NOISE = re.compile(r"menu|nav|gnb|lnb|snb|header|footer|quick|banner|location|util|sitemap|skip", re.I)


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


def crawl_web(f, org, board, pages, page_size, *, make_record, strip_ellipsis):
    cfg = board["web"]
    html = f.get(cfg["list_url"])
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(cfg["row"]) if cfg.get("row") else (soup.select("tbody tr") or soup.select("ul > li"))
    out, seen = [], set()
    for row in rows:
        hit = _title_link(row)
        if not hit:
            continue
        a, title_raw = hit
        href = (a.get("href") or "").strip()
        if cfg.get("id_re"):
            m = re.search(cfg["id_re"], str(row))
            if not m:
                continue
            detail = cfg["detail_url"].format(id=m.group(1))
        elif href and not href.lower().startswith(("javascript", "#")):
            detail = up.urljoin(cfg["list_url"], href)
        else:
            continue
        if detail in seen:
            continue
        seen.add(detail)
        title, truncated = strip_ellipsis(title_raw)
        rec = make_record(org, board, title=title, notice_no="", posted_at=_date(row.get_text(" ")), dept="",
                          detail_url=detail, key=detail)
        rec["_truncated"] = truncated
        out.append(rec)
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


def parse_web_detail(html, rec, host, *, board, page_text, clean):
    cfg = board["web"]
    soup = BeautifulSoup(html, "html.parser")
    atts, seen = [], set()
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        js = href + " " + (a.get("onclick") or "")
        name = clean(a.get_text(" ")) or clean(a.get("title"))
        m = FILE_EXT.search(name or "")
        if not m:
            continue
        name = name[: m.end()].strip()  # '공고문.hwpx (35KB) 미리보기' → '공고문.hwpx'
        url = ""
        if cfg.get("file_re"):
            fm = re.search(cfg["file_re"], js)
            if fm:
                url = cfg["file_url"].format(id=fm.group(1))
        if not url and href and not href.lower().startswith(("javascript", "#")) and (FILE_HREF.search(href) or FILE_EXT.search(href)):
            url = up.urljoin(rec["detail_url"], href)
        if url and url not in seen:
            seen.add(url)
            atts.append({"name": name, "url": url})
    node = _content(soup, cfg)
    text = page_text(node)
    if rec.get("_truncated"):
        hit = next((l for l in text.split("\n") if l.startswith(rec["title"])), "")
        if hit:
            rec["title"] = hit
    if not rec["posted_at"]:
        rec["posted_at"] = _date(text[:500])
    rec["body_text"] = text[:3000]
    rec["attachments"] = atts
