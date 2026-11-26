#!/usr/bin/env python3
"""
把 data/items.json（＋ data/plain.json 的白話摘要）轉成 App 用的公告格式。

  python3 export_announcements.py --out public/announcements.json              # 給 App 自動下載的公告檔
  python3 export_announcements.py --out ../roadmate/assets/announcements.json   # 更新 App 內建的備用公告

輸出檔如果已經存在，會把裡面還沒過期（60 天內）的舊公告併進來，所以每天跑一次就能累積。
"""
import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


MAX_AGE_DAYS = 60
MAX_ITEMS = 80


def official_points(explanation: str, limit: int = 4, width: int = 90):
    """沒有白話摘要時，從官方「總說明」取出修正要點（一、二、三…）當作條列。"""
    out = []
    for line in (explanation or "").split("\n"):
        m = re.match(r"^[一二三四五六七八九十]{1,3}、(.+)$", line.strip())
        if not m:
            continue
        text = re.sub(r"（[^（）]*條[^（）]*）$", "", m.group(1)).strip()   # 去掉句尾的（修正條文第○條）
        if text:
            out.append(text if len(text) <= width else text[: width - 1] + "…")
        if len(out) >= limit:
            break
    return out


def build(today=None):
    items = json.loads((ROOT / "data/items.json").read_text(encoding="utf-8"))["items"]
    plain_path = ROOT / "data/plain.json"
    plain = json.loads(plain_path.read_text(encoding="utf-8")) if plain_path.exists() else {}
    # 自動摘要（summarize.py 產生）墊底，手寫的 plain.json 優先
    try:
        auto = json.loads((ROOT / "data/plain_auto.json").read_text(encoding="utf-8"))
    except Exception:
        auto = {}
    for k, v in (auto.items() if isinstance(auto, dict) else []):
        if isinstance(v, dict) and v.get("points") and not (plain.get(k) or {}).get("points"):
            plain[k] = v
    cutoff = ((today or date.today()) - timedelta(days=MAX_AGE_DAYS)).isoformat()
    out = []
    for it in items:
        if not it.get("date") or not it.get("title") or it["date"] < cutoff:
            continue
        p = plain.get(it["id"]) or {}
        points, points_from = p.get("points") or [], "ai" if p.get("points") else ""
        if not points:
            points = official_points(it.get("explanation", ""))
            points_from = "official" if points else ""
        out.append({
            "id": it["id"],
            "published": it["date"],
            "agency": (it.get("agency") or "").replace(";", "、"),
            "kind": it.get("kind") or "",
            "title": " ".join(it["title"].split()),
            "headline": p.get("headline") or " ".join(it["title"].split()),
            "points": points,
            "pointsFrom": points_from,
            "effective": p.get("effective") or "",
            "impact": p.get("impact") or "",
            "url": it.get("url") or "",
            "audience": it.get("audience") or "",
            "topics": it.get("topics") or [],
            "region": it.get("region") or "全國",
        })
    out.sort(key=lambda x: x["published"], reverse=True)
    return out[:MAX_ITEMS]


def merge_previous(rows, out_path: Path, today=None):
    """把上一次輸出的公告併進來。來源網站的列表只保留最近幾則，舊的要靠上一份檔案留住。"""
    try:
        old = json.loads(out_path.read_text(encoding="utf-8")).get("items", [])
    except Exception:
        return rows
    cutoff = ((today or date.today()) - timedelta(days=MAX_AGE_DAYS)).isoformat()
    have = {r["id"] for r in rows}
    kept = [o for o in old if isinstance(o, dict) and o.get("id") not in have and str(o.get("published", "")) >= cutoff]
    merged = rows + kept
    merged.sort(key=lambda x: x["published"], reverse=True)
    return merged[:MAX_ITEMS]


def write_feed(rows, out_path: Path, now=None):
    # App 讀的格式：generatedAt 是這份資料產生的時間，App 會顯示「資料更新於」
    stamp = (now or datetime.now(timezone(timedelta(hours=8)))).isoformat(timespec="seconds")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"generatedAt": stamp, "items": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="輸出 JSON 檔路徑")
    ap.add_argument("--no-merge", action="store_true", help="不要併入上一次輸出的舊公告")
    args = ap.parse_args()
    out = Path(args.out)
    rows = build()
    if not args.no_merge:
        rows = merge_previous(rows, out)
    write_feed(rows, out)
    print(f"已寫出 {len(rows)} 則 → {args.out}")
