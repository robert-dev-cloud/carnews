#!/usr/bin/env python3
"""把 data/items.json + data/plain.json 合併，產生可直接開啟的 data/page.html。"""
import glob
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
items = json.loads((ROOT / "data/items.json").read_text(encoding="utf-8"))
plain = json.loads((ROOT / "data/plain.json").read_text(encoding="utf-8"))

days = sorted(Path(f).stem for f in glob.glob(str(ROOT / "cache/gazette/*.xml")))


def roc(day):  # '115-11-03' -> '2026-11-03'
    y, m, d = day.split("-")
    return f"{int(y) + 1911}-{m}-{d}"


out = {
    "generated_at": items["generated_at"],
    "fetched_counts": items["fetched_counts"],
    "range": [roc(days[0]), roc(days[-1])] if days else ["", ""],
    "items": [],
}
for it in items["items"]:
    out["items"].append({
        "id": it["id"], "date": it["date"], "agency": it["agency"], "kind": it["kind"],
        "title": it["title"], "url": it["url"], "topics": it["topics"], "audience": it["audience"],
        "explanation": it.get("explanation", ""), "plain": plain.get(it["id"]),
    })

payload = json.dumps(out, ensure_ascii=False).replace("</", "<\\/")
html = (ROOT / "page_template.html").read_text(encoding="utf-8").replace("/*__DATA__*/", payload)
(ROOT / "data/page.html").write_text(html, encoding="utf-8")
missing = [i["id"] for i in out["items"] if not i["plain"]]
print(f"page.html 已產生：{len(out['items'])} 則，缺白話摘要 {len(missing)} 則 {missing}")
