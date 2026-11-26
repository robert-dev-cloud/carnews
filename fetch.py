#!/usr/bin/env python3
"""
車主法規消息擷取程式（原型）

來源：
  1. 行政院公報 開放資料（每日 XML，含法規修正、草案預告、公告）
  2. 交通部 RSS（新聞稿、公告訊息）
  3. 環境部新聞專區（HTML）

只用 Python 標準函式庫。執行：
  python3 fetch.py            # 抓全部來源，輸出 data/items.json
  python3 fetch.py --offline  # 只用 cache 裡已下載的檔案重新篩選

每次執行都會把原始檔存到 cache/，同一天的公報不會重複下載。
"""
import argparse
import hashlib
import html
import io
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from rules import classify

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
DATA = ROOT / "data"
UA = "CarNewsPrototype/0.1 (personal research; low frequency)"
TPE = timezone(timedelta(hours=8))
LOCAL_SOURCES = ("臺北市交通警察大隊", "新北市政府交通局")
LOCAL_TRAFFIC_WORDS = ["交通管制", "交通疏導", "管制措施", "改道", "封閉", "測速", "科技執法",
                       "取締", "舉發", "違規", "違停", "車牌", "噪音", "改裝", "拖吊", "路口",
                       "交流道", "匝道", "通車", "停車", "施工", "塞車", "壅塞", "疏運", "號誌", "車道"]
DELAY = 1.0  # 每個請求之間停一秒，不對政府網站造成負擔


def http_get(url: str, binary: bool = False, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for attempt in range(3):  # 政府網站偶爾會斷線，最多試三次
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            break
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
    else:
        raise last
    time.sleep(DELAY)
    return data if binary else data.decode("utf-8", errors="replace")


def strip_tags(s: str) -> str:
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t　\xa0]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def roc_to_iso(s: str):
    """'中華民國115年11月18日' 或 '115-11-18' -> '2026-11-18'；失敗回傳 None"""
    m = re.search(r"(\d{2,3})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})", s or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def make_id(source: str, key: str) -> str:
    return source + "-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 行政院公報

GAZ_BASE = "https://gazette.nat.gov.tw/egFront/"


def gazette_zip_links(page_html: str):
    out = {}
    for href in re.findall(r'href="(fileView\.do\?[^"]*zipfile=(\d{3}-\d{2}-\d{2})\.zip)"', page_html):
        out[href[1]] = GAZ_BASE + html.unescape(href[0])
    return out


def fetch_gazette(offline: bool):
    gdir = CACHE / "gazette"
    gdir.mkdir(parents=True, exist_ok=True)
    errors = []
    if not offline:
        try:
            links = gazette_zip_links(http_get(GAZ_BASE + "openData03.do"))
            for day, url in sorted(links.items()):
                target = gdir / f"{day}.xml"
                if target.exists():
                    continue
                try:
                    z = zipfile.ZipFile(io.BytesIO(http_get(url, binary=True, timeout=180)))
                    name = next(n for n in z.namelist() if n.endswith(".xml"))
                    target.write_bytes(z.read(name))
                except Exception as e:  # 單日失敗不影響其他天
                    errors.append(f"gazette {day}: {e}")
        except Exception as e:
            errors.append(f"gazette index: {e}")

    items = []
    for f in sorted(gdir.glob("*.xml")):
        try:
            root = ET.fromstring(f.read_bytes())
        except ET.ParseError as e:
            errors.append(f"gazette parse {f.name}: {e}")
            continue
        for rec in root.iter("Record"):
            g = lambda tag: (rec.findtext(tag) or "").strip()
            title = g("Title")
            if not title:
                continue
            body = strip_tags(g("HTMLContent"))
            style = g("Doc_Style_LName")
            is_draft = "草案" in style or "預告" in title[:40]
            items.append({
                "id": make_id("gaz", g("MetaId") or title),
                "source": "行政院公報",
                "agency": g("PubGovName") or g("PubGov"),
                "kind": "草案預告" if is_draft else (style or "公告"),
                "title": title,
                "date": roc_to_iso(g("Date_Published")) or roc_to_iso(f.stem),
                "url": g("GazetteHTML"),
                "official_summary": g("ThemeSubject") or g("Explain"),
                "comment_deadline": roc_to_iso(g("Comment_Deadline")),
                "keywords": [k for k in g("Keyword").split(";") if k],
                "text": body,
            })
    return items, errors


def pdf_to_text(data: bytes) -> str:
    """用 pdftotext（poppler-utils）把 PDF 轉文字；沒安裝就回傳空字串。"""
    if not shutil.which("pdftotext"):
        return ""
    try:
        r = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, timeout=60)
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def tidy_explain(raw: str) -> str:
    """從『總說明及對照表』PDF 取出總說明（修正要點），丟掉後面的逐條對照表。"""
    t = re.sub(r"行政院公報\s*第\d+卷\s*第\d+期\s*\d{8}\s*\S+篇", "", raw)
    t = re.sub(r"[ \t\u3000\r\n\f]+", "", t)   # 中文排版用空白和換行對齊，全部拿掉
    i = t.find("總說明")
    if i < 0:
        return ""
    t = t[i + 3:]
    for stop in ("對照表", "附表、"):      # 後面是逐條對照，不需要
        j = t.find(stop)
        if j > 0:
            t = t[:j]
    # 對照表的標題會殘留半句，砍到最後一個句號或右括號
    k = max(t.rfind("。"), t.rfind("）"), t.rfind(")"))
    t = t[: k + 1] if k > 0 else t
    # 「一、二、三、」各自成行，方便閱讀
    t = re.sub(r"(?<=[。）)])(?=[一二三四五六七八九十]{1,3}、)", "\n", t)
    return t[:1500].strip()


def fetch_explanation(item: dict, offline: bool) -> str:
    """抓公報網頁上的『總說明及對照表』PDF（通常是 images/AA.pdf），回傳整理過的總說明文字。"""
    xdir = CACHE / "explain"
    xdir.mkdir(parents=True, exist_ok=True)
    target = xdir / f"{item['id']}.txt"
    if target.exists():
        return target.read_text(encoding="utf-8")
    if offline or not item.get("url"):
        return ""
    try:
        page = http_get(item["url"])
        base = item["url"].rsplit("/", 1)[0] + "/"
        text = ""
        for href in re.findall(r'href="([^"]+\.pdf)"', page, flags=re.I)[:4]:
            raw = pdf_to_text(http_get(urllib.parse.urljoin(base, href), binary=True, timeout=120))
            if "總說明" in re.sub(r"\s+", "", raw):
                text = tidy_explain(raw)
                break
        target.write_text(text, encoding="utf-8")
        return text
    except Exception:
        return ""


# ---------------------------------------------------------------- 交通部 RSS

MOTC_FEEDS = {
    "新聞稿": "https://www.motc.gov.tw/ch/app/rss/News",
    "公告": "https://www.motc.gov.tw/ch/app/rss/Bulletin",
}


def parse_rss_date(s: str):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = re.search(r"(20\d\d)[-/](\d{1,2})[-/](\d{1,2})", s)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def fetch_motc(offline: bool):
    mdir = CACHE / "motc"
    mdir.mkdir(parents=True, exist_ok=True)
    errors, seen = [], {}
    for kind, url in MOTC_FEEDS.items():
        if not offline:
            try:
                raw = http_get(url, binary=True)
                # RSS 只給最新 10 則，所以每次都存一份，累積歷史
                stamp = datetime.now(TPE).strftime("%Y%m%d-%H%M")
                (mdir / f"{kind}-{stamp}.xml").write_bytes(raw)
            except Exception as e:
                errors.append(f"motc {kind}: {e}")
        for f in sorted(mdir.glob(f"{kind}-*.xml")):
            try:
                root = ET.fromstring(f.read_bytes())
            except ET.ParseError as e:
                errors.append(f"motc parse {f.name}: {e}")
                continue
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                if not title:
                    continue
                desc = strip_tags(it.findtext("description") or "")
                seen[link or title] = {
                    "id": make_id("motc", link or title),
                    "source": "交通部",
                    "agency": "交通部",
                    "kind": kind,
                    "title": title,
                    "date": parse_rss_date(it.findtext("pubDate")),
                    "url": link,
                    "official_summary": desc[:300],
                    "comment_deadline": None,
                    "keywords": [],
                    "text": desc,
                }
    return list(seen.values()), errors


# ---------------------------------------------------------------- 環境部新聞

MOENV_HOME = "https://enews.moenv.gov.tw/"


def fetch_moenv(offline: bool, max_new: int = 40):
    edir = CACHE / "moenv"
    edir.mkdir(parents=True, exist_ok=True)
    errors = []
    if not offline:
        try:
            home = http_get(MOENV_HOME)
            ids = sorted({int(i) for i in re.findall(r"/moenv-news/zh-tw/News/(\d+)", home)}, reverse=True)
            for nid in ids[:max_new]:
                target = edir / f"{nid}.html"
                if target.exists():
                    continue
                try:
                    target.write_text(http_get(f"{MOENV_HOME}moenv-news/zh-tw/News/{nid}"), encoding="utf-8")
                except Exception as e:
                    errors.append(f"moenv {nid}: {e}")
        except Exception as e:
            errors.append(f"moenv index: {e}")

    items = []
    for f in sorted(edir.glob("*.html")):
        page = f.read_text(encoding="utf-8", errors="replace")
        og = re.search(r'property="og:title" content="([^"]*)"', page)
        title = html.unescape(html.unescape(og.group(1))).strip(" 【】") if og else ""
        if not title:
            continue
        main = re.search(r"<main\b.*?</main>", page, re.S)
        text = strip_tags(main.group(0) if main else page)
        # 頁尾的「智慧推薦」是別篇新聞的標題，會造成誤判，切掉
        text = text.split("智慧推薦")[0]
        dm = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', page) or re.search(r"(1\d{2})-(\d{2})-(\d{2})", text)
        if dm and dm.lastindex == 1:
            date = dm.group(1)
        elif dm:
            date = roc_to_iso(dm.group(0))
        else:
            date = None
        items.append({
            "id": make_id("moenv", f.stem),
            "source": "環境部",
            "agency": "環境部",
            "kind": "新聞稿",
            "title": title,
            "date": date,
            "url": f"{MOENV_HOME}moenv-news/zh-tw/News/{f.stem}",
            "official_summary": "",
            "comment_deadline": None,
            "keywords": [],
            "text": text,
        })
    return items, errors


# ---------------------------------------------------------------- 臺北市交通警察大隊

TPE_POLICE_BASE = "https://td.police.gov.taipei/"
TPE_POLICE_NEWS = TPE_POLICE_BASE + "News.aspx?n=B19CFD7B4346611D&sms=72544237BBE4C5F6"


def parse_taipei_police(page: str):
    """新聞稿列表是一張表格：標題（含連結）、發布日期（民國年）、發布機關。"""
    items = []
    for row in re.findall(r"<tr>(.*?)</tr>", page, flags=re.S):
        a = re.search(r'<a href="(News_Content\.aspx\?[^"]+)"[^>]*>(.*?)</a>', row, flags=re.S)
        d = re.search(r'data-title="發布日期"[^>]*>\s*<span>([^<]+)</span>', row)
        if not a or not d:
            continue
        title = strip_tags(a.group(2))
        date = roc_to_iso(d.group(1))
        if not title or not date:
            continue
        link = TPE_POLICE_BASE + html.unescape(a.group(1))
        items.append({
            "id": make_id("tpepolice", link),
            "source": "臺北市交通警察大隊",
            "agency": "臺北市政府警察局交通警察大隊",
            "kind": "新聞稿",
            "title": title,
            "date": date,
            "url": link,
            "official_summary": "",
            "comment_deadline": None,
            "keywords": [],
            "text": title,
            "region": "臺北市",
        })
    return items


def fetch_taipei_police(offline: bool):
    tdir = CACHE / "tpepolice"
    tdir.mkdir(parents=True, exist_ok=True)
    errors = []
    if not offline:
        try:
            stamp = datetime.now(TPE).strftime("%Y%m%d")
            (tdir / f"news-{stamp}.html").write_text(http_get(TPE_POLICE_NEWS), encoding="utf-8")
        except Exception as e:
            errors.append(f"taipei police: {e}")
    seen = {}
    for f in sorted(tdir.glob("news-*.html")):   # 列表只有最新 20 則，每天存一份累積
        for it in parse_taipei_police(f.read_text(encoding="utf-8", errors="replace")):
            seen[it["id"]] = it
    return list(seen.values()), errors


# ---------------------------------------------------------------- 新北市政府交通局

NTPC_BASE = "https://www.traffic.ntpc.gov.tw/"
NTPC_NEWS = NTPC_BASE + "home.jsp?id=54fa46e9e522dde4"


def any_date(s: str):
    """西元 2026-11-20 / 2026/11/20 或民國 115-11-20 都接受。"""
    m = re.search(r"(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})", s or "")
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return roc_to_iso(s)


def parse_ntpc_transport(page: str):
    """新聞稿列表：每一列是一個 <a>，title 屬性是標題，裡面有 class 含 date 的 <span>。"""
    items = []
    for href, title, inner in re.findall(
        r'<a href="(home\.jsp\?id=54fa46e9e522dde4&(?:amp;)?act=[^"]*dataserno=[0-9a-f]+[^"]*)"\s+title="([^"]*)"[^>]*>(.*?)</a>',
        page, flags=re.S,
    ):
        d = re.search(r'<span class="date[^"]*">\s*([^<]+?)\s*</span>', inner)
        title = html.unescape(title).strip()
        date = any_date(d.group(1)) if d else None
        if not title or not date:
            continue
        link = NTPC_BASE + html.unescape(href)
        items.append({
            "id": make_id("ntpctraffic", link),
            "source": "新北市政府交通局",
            "agency": "新北市政府交通局",
            "kind": "新聞稿",
            "title": title,
            "date": date,
            "url": link,
            "official_summary": "",
            "comment_deadline": None,
            "keywords": [],
            "text": title,
            "region": "新北市",
        })
    return items


def fetch_ntpc_transport(offline: bool):
    ndir = CACHE / "ntpctraffic"
    ndir.mkdir(parents=True, exist_ok=True)
    errors = []
    if not offline:
        try:
            stamp = datetime.now(TPE).strftime("%Y%m%d")
            (ndir / f"news-{stamp}.html").write_text(http_get(NTPC_NEWS), encoding="utf-8")
        except Exception as e:
            errors.append(f"ntpc transport: {e}")
    seen = {}
    for f in sorted(ndir.glob("news-*.html")):
        for it in parse_ntpc_transport(f.read_text(encoding="utf-8", errors="replace")):
            seen[it["id"]] = it
    return list(seen.values()), errors


# ---------------------------------------------------------------- 主流程

def excerpt(text: str, n: int = 160) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= n else t[:n].rstrip() + "…"


def run(offline: bool):
    DATA.mkdir(exist_ok=True)
    all_items, all_errors, counts = [], [], {}
    for name, fn in (("行政院公報", fetch_gazette), ("交通部", fetch_motc), ("環境部", fetch_moenv),
                     ("臺北市交通警察大隊", fetch_taipei_police), ("新北市政府交通局", fetch_ntpc_transport)):
        items, errs = fn(offline)
        counts[name] = len(items)
        all_items += items
        all_errors += errs

    relevant = []
    for it in all_items:
        c = classify(it["title"], it.get("official_summary", ""), it.get("text", ""), it.get("keywords", []))
        if it["source"] in LOCAL_SOURCES:
            # 這個列表混了很多警政宣導（尋人、防詐），只收標題跟用路有關的
            hit = [w for w in LOCAL_TRAFFIC_WORDS if w in it["title"]]
            if "名單" in it["title"]:   # 點名個人的公告不轉載
                hit = []
            if any(w in it["title"] for w in ("移送", "偵辦", "查獲", "逮", "落網")):   # 個案的刑事新聞不是用路資訊
                hit = []
            if any(w in it["title"] for w in ("金質獎", "金擘獎", "榮獲", "獲獎")):   # 得獎新聞不是用路資訊
                hit = []
            c = {"relevant": bool(hit), "score": len(hit) * 3, "topics": c["topics"] or ["地方交通消息"],
                 "vehicles": c["vehicles"] or ["汽車", "機車"], "audience": "一般車主", "matched": hit}
        if not c["relevant"]:
            continue
        out = {k: v for k, v in it.items() if k != "text"}
        out["official_summary"] = excerpt(it.get("official_summary") or it.get("text", ""))
        out.update(topics=c["topics"], vehicles=c["vehicles"], audience=c["audience"], score=c["score"], matched=c["matched"])
        out["region"] = it.get("region", "全國")
        out["explanation"] = fetch_explanation(it, offline) if it["source"] == "行政院公報" else ""
        # 標題看不出來、但總說明通篇在講職業駕駛或運輸業的，改標為營業車
        if out["audience"] == "一般車主" and sum(out["explanation"].count(w) for w in ("職業駕駛", "汽車運輸業", "客運業")) >= 3:
            out["audience"] = "營業車"
        relevant.append(out)

    relevant.sort(key=lambda x: (x["date"] or "", x["score"]), reverse=True)
    result = {
        "generated_at": datetime.now(TPE).isoformat(timespec="seconds"),
        "fetched_counts": counts,
        "relevant_count": len(relevant),
        "errors": all_errors,
        "items": relevant,
    }
    (DATA / "items.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="不連網，只用 cache")
    args = ap.parse_args()
    r = run(args.offline)
    print(f"抓到：{r['fetched_counts']}  →  車主相關 {r['relevant_count']} 則")
    for e in r["errors"]:
        print("  [錯誤]", e, file=sys.stderr)
    for it in r["items"][:40]:
        print(f"{it['date']} [{it['source']}/{it['kind']}] {'、'.join(it['topics'])} | {it['title'][:70]}")
