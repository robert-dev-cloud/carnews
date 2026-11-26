#!/usr/bin/env python3
"""
清掉伺服器上太舊的回報（預設 7 天前）和它們的照片，再清掉儲存空間裡沒有人在用的檔案。

  SUPABASE_URL=https://xxxx.supabase.co SUPABASE_SERVICE_ROLE_KEY=... python3 purge.py

- 兩個環境變數沒設就什麼都不做（正常結束）。加上 PURGE_REQUIRED=1 的話，沒設會當成失敗
  （專門清資料的排程用這個，忘了設定時 GitHub 上會顯示紅色的叉，才不會以為有在清）。
- SUPABASE_SERVICE_ROLE_KEY 是最高權限的金鑰：只能放在 GitHub 的 Secrets 裡，
  絕對不要寫進程式、不要放進 App、不要貼給任何人。
- 這支程式只做兩件事：
  1. 呼叫資料庫的 purge_old_reports()，再刪掉它回傳的照片檔。
  2. 呼叫資料庫的 orphan_files()，刪掉它列出的檔案：上傳了卻沒附在回報上的照片、被管理者刪掉的回報留下的照片、
     換頭像時沒刪成功的舊頭像。只會刪上傳超過一小時、而且資料庫確認沒有人在用的。
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

BUCKET = "report-photos"
# orphan_files() 可能列出這幾個儲存空間的檔案；其他名字一律不理
ORPHAN_BUCKETS = ("report-photos", "avatars")
PHOTO_NAME = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jpg$")
BATCH = 100


def _request(method, url, key, payload, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method=method,
        headers={"content-type": "application/json", "apikey": key, "authorization": "Bearer " + key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else None


def _why(e, key):
    """把伺服器回的錯誤訊息整理成一小段可以印出來的字（給人看原因用）。金鑰絕對不會被印出來。"""
    try:
        raw = e.read(600).decode("utf-8", "replace")
    except Exception:
        return ""
    try:
        data = json.loads(raw)
        text = " / ".join(str(data[k]) for k in ("message", "msg", "error", "hint", "code") if isinstance(data, dict) and data.get(k))
    except Exception:
        text = raw
    text = re.sub(r"\s+", " ", text or raw).strip()
    if key:
        text = text.replace(key, "***")
    # 萬一訊息裡有任何長得像金鑰或登入憑證的東西，也一律遮掉
    text = re.sub(r"(sb_(secret|publishable)_[A-Za-z0-9_\-]+|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)", "***", text)
    return text[:200]


def clean_url(url: str):
    """只接受 https 的 Supabase 專案網址（測試時允許 http://127.0.0.1）。"""
    url = (url or "").strip().rstrip("/")
    if re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", url) or re.fullmatch(r"http://127\.0\.0\.1:\d+", url):
        return url
    return None


def run(env=None, days=7, log=print):
    env = os.environ if env is None else env
    raw_url = (env.get("SUPABASE_URL") or "").strip()
    key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not raw_url or not key:
        if (env.get("PURGE_REQUIRED") or "").strip() not in ("", "0"):
            missing = "、".join(n for n, v in (("SUPABASE_URL", raw_url), ("SUPABASE_SERVICE_ROLE_KEY", key)) if not v)
            log(f"還沒設定 {missing}。到這個 repository 的 Settings → Secrets and variables → Actions 新增（名字要一模一樣）。")
            return 1
        log("沒有設定 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY，略過清除。")
        return 0
    url = clean_url(raw_url)
    if not url:
        log("SUPABASE_URL 格式不對，應該像 https://xxxx.supabase.co")
        return 1
    try:
        rows = _request("POST", url + "/rest/v1/rpc/purge_old_reports", key, {"p_days": int(days)})
    except urllib.error.HTTPError as e:
        log(f"清除失敗：資料庫回應 {e.code}  {_why(e, key)}".rstrip())
        if e.code in (401, 403):
            log("通常是金鑰不對：SUPABASE_SERVICE_ROLE_KEY 要貼 Supabase 後台的 secret key（sb_secret_ 開頭），不是 App 用的那把 sb_publishable_。")
        elif e.code == 404:
            log("通常是網址不對，或資料庫還沒跑過 schema.sql。")
        return 1
    except Exception as e:
        log(f"清除失敗：連不上資料庫（{type(e).__name__}）")
        return 1
    # 只刪檔名格式正確的，避免資料庫裡有奇怪的值時刪到別的東西
    photos = sorted({p for p in (rows or []) if isinstance(p, str) and PHOTO_NAME.match(p)})
    log(f"舊回報已清除。要刪的照片：{len(photos)} 張")
    failed = _delete_files(url, key, BUCKET, photos, log)

    # 沒有人在用的檔案
    orphan_error = False
    try:
        listed = _request("POST", url + "/rest/v1/rpc/orphan_files", key, {"p_limit": 1000})
    except urllib.error.HTTPError as e:
        listed = []
        if e.code == 404:
            # 資料庫還是舊版，沒有這個函式：不算失敗，提醒一下就好
            log("資料庫還沒有 orphan_files()（到 Supabase 的 SQL Editor 重跑一次 schema.sql 就會有），這次先不清沒人用的檔案。")
        else:
            orphan_error = True
            log(f"列不出沒人用的檔案：資料庫回應 {e.code}  {_why(e, key)}".rstrip())
    except Exception as e:
        listed = []
        orphan_error = True
        log(f"列不出沒人用的檔案：連不上資料庫（{type(e).__name__}）")
    for bucket in ORPHAN_BUCKETS:
        names = sorted({
            r.get("name") for r in (listed or [])
            if isinstance(r, dict) and r.get("bucket") == bucket and isinstance(r.get("name"), str) and PHOTO_NAME.match(r["name"])
        })
        if names:
            log(f"{bucket} 裡沒人用的檔案：{len(names)} 個")
            failed += _delete_files(url, key, bucket, names, log)

    if failed:
        log("沒刪成功的檔案會留在儲存空間裡，請到 Supabase 後台的 Storage 手動檢查。")
    if failed or orphan_error:
        return 1
    log("完成。")
    return 0


def _delete_files(url, key, bucket, names, log):
    """分批刪掉某個儲存空間裡的檔案，回傳沒刪成功的數量。"""
    failed = 0
    for i in range(0, len(names), BATCH):
        chunk = names[i:i + BATCH]
        try:
            _request("DELETE", f"{url}/storage/v1/object/{bucket}", key, {"prefixes": chunk})
        except urllib.error.HTTPError as e:
            failed += len(chunk)
            log(f"{bucket} 有 {len(chunk)} 個檔案沒刪成功：儲存空間回應 {e.code}  {_why(e, key)}".rstrip())
        except Exception as e:
            failed += len(chunk)
            log(f"{bucket} 有 {len(chunk)} 個檔案沒刪成功（{type(e).__name__}）")
    return failed


if __name__ == "__main__":
    sys.exit(run())
