# carnews：政府公告自動擷取

每天自動抓政府網站，篩出跟開車、騎車有關的公告，輸出成 `public/announcements.json`。
「車友路況回報」App 會自己去下載這個檔。

## 資料來源

| 來源 | 方式 | 狀況 |
|---|---|---|
| 行政院公報 | 官方開放資料，每天一包 XML | 穩定。全國的法規修正、草案預告都在這裡 |
| 公報網頁上的「總說明」PDF | 每則公告頁的附件 | 可抓。用來產生「官方說明節錄」 |
| 交通部 | 官方 RSS（新聞稿、公告） | 可抓 |
| 環境部新聞專區 | 解析網頁 | 可抓，網站改版就要修程式 |
| 臺北市交通警察大隊 | 解析新聞稿列表 | 可抓。只收交通管制、執法相關的 |
| 新北市政府交通局 | 解析新聞稿列表 | 可抓。只收道路、停車、管制相關的 |
| 新北市交通警察大隊 | 無 | 網站上的「新聞」其實是罰單公示送達，含個人資料，不擷取 |
| 公路局 | 無 | 網站有防機器人機制，不擷取 |

地方來源用關鍵字篩選，會漏也可能收到不太相關的。規則在 `fetch.py` 的 `LOCAL_TRAFFIC_WORDS`。

---

## 設定每天自動執行（用 GitHub，免費）

做一次就好。以下一次貼一行。

**1. 到 https://github.com 註冊帳號，建立一個新的 repository**
- 名稱填 `carnews`
- 選 **Public**（公開）。App 要能直接下載檔案，所以必須公開。裡面沒有任何金鑰或個人資料
- 其他選項都不要勾

**2. 在電腦上把這個資料夾推上去**（把 `你的帳號` 換成你的 GitHub 帳號）

```
cd carnews
```
```
git init -b main
```
```
git add .
```
```
git commit -m "first commit"
```
```
git remote add origin https://github.com/你的帳號/carnews.git
```
```
git push -u origin main
```

**3. 確認排程有開**
- 到 GitHub 上這個 repository → 上方 **Actions** 分頁
- 如果看到要你啟用的按鈕，按下去
- 左邊點「更新公告」→ 右邊 **Run workflow** → 再按一次綠色的 Run workflow，手動跑第一次
- 等一兩分鐘，出現綠色勾勾就成功

**4. 把網址填進 App**

網址是（把 `你的帳號` 換掉）：
```
https://raw.githubusercontent.com/你的帳號/carnews/main/public/announcements.json
```
先用瀏覽器開這個網址，看得到一堆文字就對了。然後填到 `roadmate/app.json` 的 `announcementsUrl`。

之後它會在台灣時間每天 06:17、12:17、18:17 自動跑。

**帳號請開兩步驟驗證**：右上角頭像 → Settings → Password and authentication → Enable two-factor authentication。這個 repository 之後會放金鑰，帳號被盜等於金鑰外洩。

**注意**
- GitHub 的排程偶爾會延遲幾分鐘到幾十分鐘，屬正常。
- repository 連續 60 天沒有任何活動，GitHub 會自動停用排程。這支程式每次跑都會提交一次，正常情況不會發生；萬一停了，到 Actions 頁面按啟用即可。
- 某個政府網站改版時，那個來源會抓不到，其他來源不受影響。到 Actions 頁面看紀錄可以知道是哪個。
- 公告數少於 3 則時程式會中止、不更新檔案，避免把壞掉的結果發布出去。

---

## 在自己電腦上跑

需要 Python 3.9 以上和 `pdftotext`。Ubuntu 安裝：
```
sudo apt install poppler-utils
```
跑一次完整流程：
```
bash run_daily.sh
```
跑測試：
```
python3 -m unittest test_rules test_auto
```
只想看抓到什麼：
```
python3 fetch.py
```

---

## 白話摘要

顯示順序：

1. `data/plain.json`：人工確認過的摘要，最優先
2. `data/plain_auto.json`：`summarize.py` 請 AI 自動寫的
3. 都沒有：節錄官方「總說明」的修正要點，App 上標示「官方說明節錄」
4. 連總說明都抓不到：只有標題和原文連結

### 開啟 AI 自動摘要（選做，會有費用）

1. 到 https://console.anthropic.com 申請 API 金鑰，並設定每月花費上限。
2. GitHub 上這個 repository → **Settings → Secrets and variables → Actions → New repository secret**
   - Name 填 `ANTHROPIC_API_KEY`，Secret 貼上金鑰

沒設定這個 Secret 就不會做 AI 摘要，其他照常。

它怎麼避免亂寫：
- 只處理有官方「總說明」原文的公告，不讓 AI 只看標題猜
- AI 寫出的數字（兩位數以上），原文裡一定要找得到，否則整份丟掉，改用官方說明節錄
- 有網址、格式不對、太長的也丟掉
- 每次最多處理 8 則；同一則不會重複問

**限制**：程式只能檢查數字和格式，檢查不了語意。AI 還是可能把意思講偏，請偶爾抽查 `data/plain_auto.json`。看到寫錯的，把正確版本寫進 `data/plain.json`（會蓋過自動的）。想讓某一則重新產生，就把它從 `plain_auto.json` 刪掉。

費用：每則公告大約幾千字，用預設的小模型，一個月通常不到幾十元台幣；實際以 Anthropic 帳單為準。要換模型，在 workflow 裡加環境變數 `CARNEWS_MODEL`。

---

## 自動清除 7 天前的回報（建議做）

有自己的排程（`.github/workflows/purge.yml`，台灣時間每天 04:23），和抓公告的排程分開，公告那邊出問題不會影響清除。`purge.py` 每次跑的時候，會請資料庫刪掉 7 天前的回報，再刪掉對應的照片；接著再清掉儲存空間裡**沒有人在用的檔案**（上傳了卻沒發出去的照片、被管理者刪掉的回報留下的照片、換頭像時沒刪成功的舊頭像；只刪上傳超過一小時、資料庫確認沒人用的）。（App 只顯示 2 天內的回報，7 天是留一點緩衝；要改天數，改 `purge.py` 裡 `run()` 的 `days`。）

在這個 repository 的 **Settings → Secrets and variables → Actions → New repository secret** 新增兩個 Secret（名字要一模一樣）：

- `SUPABASE_URL`：像 `https://abcdefgh.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY`：Supabase 後台 Project Settings → API Keys 的 **secret key**（`sb_secret_` 開頭；舊版後台叫 service_role key，也可以用）。不是 App 用的那把 `sb_publishable_`

**service_role key 是最高權限的金鑰。只能貼在 GitHub 的 Secret 欄位裡。不要寫進任何檔案、不要放進 App、不要貼給任何人（包含貼到聊天裡）。** 放在 Secret 裡，即使 repository 是公開的別人也看不到。

設好之後到 **Actions → 清除舊資料 → Run workflow** 手動跑一次，綠色的勾就是成功；點進去看得到刪了幾個檔案。沒設定或失敗會是紅色的叉，裡面會寫原因（金鑰不會被印出來），GitHub 也會寄信。

沒設定就不清除，資料會一直累積。

注意：GitHub 規定 repository 連續 60 天沒有任何更新，排程會被自動停用（停用前會寄信）。抓公告的排程平常會自動提交新資料，所以正常不會遇到；如果收到那封信，到 Actions 頁面按一下 Enable 就好。

---

## 檔案

```
fetch.py                   擷取各來源、抓總說明、輸出 data/items.json
rules.py                   判斷全國公告是否跟車主有關、分類主題與對象
export_announcements.py    轉成 App 的公告格式，並併入上次還沒過期的舊公告
run_daily.sh               每天跑的完整流程（排程就是執行這支）
.github/workflows/update.yml   GitHub 的排程：抓公告（一天三次）
.github/workflows/purge.yml    GitHub 的排程：清除伺服器上的舊資料（一天一次）
public/announcements.json  輸出結果，App 下載的就是這個
summarize.py               請 AI 寫白話摘要（有設定金鑰才會做）
purge.py                   清除伺服器上 7 天前的回報和照片、沒人用的檔案
data/plain.json            人工確認過的白話摘要
data/plain_auto.json       AI 自動寫的白話摘要（第一次跑完才會出現）
test_rules.py、test_auto.py  測試
build_page.py、page_template.html   早期的網頁原型，可以不理
cache/                     下載過的原始檔（不會上傳到 GitHub）
```

每個請求之間停 1 秒，一天跑三次，不會對政府網站造成負擔。
