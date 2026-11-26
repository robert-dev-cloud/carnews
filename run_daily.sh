#!/usr/bin/env bash
# 每天跑一次：抓政府公告 → 更新 public/announcements.json（App 會自動下載這個檔）
# 需要 python3 和 pdftotext（Ubuntu：sudo apt install poppler-utils）
set -euo pipefail
cd "$(dirname "$0")"
python3 fetch.py
# 有設定 ANTHROPIC_API_KEY 才會做；失敗也不影響公告更新
python3 summarize.py || echo "自動摘要失敗，這次略過"
python3 export_announcements.py --out public/announcements.json
python3 - <<'PY'
import json
d = json.load(open("public/announcements.json", encoding="utf-8"))
assert isinstance(d.get("items"), list) and d.get("generatedAt"), "輸出格式不對"
assert len(d["items"]) >= 3, "公告太少，可能是來源網站出問題，這次不更新"
print("檢查通過：", len(d["items"]), "則，更新時間", d["generatedAt"])
PY
