# Tech Daily Digest

每日自動抓取科技新聞，用 CTO / 技術主管視角透過 Gemini AI 分析出 5 大必讀事件，傳送到 LINE。

## 架構

```
每日 GitHub Actions 觸發（台灣時間早上 8:00）
  → crawler.py  抓取 RSS / Sitemap / 通用網頁（過去 24 小時）
  → analyzer.py 呼叫 Gemini API 分析（單次 API call）
  → notifier.py 傳送到 LINE 個人或群組
```

## 專案結構

```
.
├── config/
│   ├── sources.json          # 新聞來源設定（RSS / Sitemap / Web）
│   └── settings.example.json # 本機設定範本
├── src/
│   ├── crawler.py            # 三合一爬蟲
│   ├── analyzer.py           # Gemini API 分析
│   ├── notifier.py           # LINE Messaging API 推播
│   └── main.py               # 主程式
├── .env.example              # 環境變數範本
├── .github/workflows/
│   └── daily.yml             # GitHub Actions 排程
└── requirements.txt
```

## 快速開始

### 1. 設定新聞來源

編輯 `config/sources.json`，支援三種來源類型：

```json
{ "sources": [
  {
    "name": "Hacker News",
    "url": "https://news.ycombinator.com/rss",
    "type": "rss",
    "category": "tech"
  },
  {
    "name": "某媒體 Sitemap",
    "url": "https://example.com/sitemap.xml",
    "type": "sitemap",
    "category": "tech"
  },
  {
    "name": "無 RSS 的網站",
    "url": "https://example.com/news",
    "type": "web",
    "category": "tech",
    "article_selector": "h2.title > a",
    "max_articles": 15
  }
]}
```

| type | 說明 | 建議 |
|---|---|---|
| `rss` | RSS / Atom feed | 優先使用，效率最高 |
| `sitemap` | XML sitemap（含 Google News 格式）| 有 sitemap 但無 RSS 時使用 |
| `web` | 通用網頁（trafilatura 自動提取）| 最後手段，每篇多一次 HTTP request |

### 2. 取得 API 金鑰

#### Gemini API Key
1. 前往 [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. 點 **Create API key**
3. 需連結 GCP 計費帳戶（每次執行費用 < $0.01 USD）

#### LINE Messaging API
1. 前往 [account.line.biz](https://account.line.biz) 建立 LINE 官方帳號
2. 進入官方帳號管理後台 → **設定 → Messaging API** → 啟用
3. 前往 [LINE Developers Console](https://developers.line.biz) 取得：
   - **Channel Access Token**（Messaging API 頁籤最底部 → Issue）
   - **Your User ID**（Basic settings 頁籤，`U` 開頭）
4. 將官方帳號加為 LINE 好友（必須加好友才能收到推播）

### 3. 本機執行

```bash
# 建立虛擬環境（macOS Apple Silicon 請用系統 Python）
/usr/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 設定環境變數
cp .env.example .env
# 編輯 .env 填入金鑰

# 只測試爬蟲（不需 API key）
.venv/bin/python3 src/main.py --crawl-only

# 測試含 AI 分析（不傳 LINE）
.venv/bin/python3 src/main.py --dry-run

# 完整執行（傳送 LINE）
.venv/bin/python3 src/main.py
```

`.env` 格式：

```env
GEMINI_API_KEY=AIzaSy-你的金鑰
GEMINI_MODEL=gemini-2.5-flash
LINE_CHANNEL_ACCESS_TOKEN=你的-Channel-Access-Token
LINE_TARGET_TYPE=user
LINE_TARGET_ID=U開頭的UserId
```

### 4. 部署到 GitHub Actions

**Repo → Settings → Secrets and variables → Actions** 新增 4 個 Secrets：

| Secret 名稱 | 說明 |
|---|---|
| `GEMINI_API_KEY` | Gemini API 金鑰 |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Channel Access Token |
| `LINE_TARGET_TYPE` | `user` 或 `group` |
| `LINE_TARGET_ID` | LINE User ID（`U`開頭）或 Group ID（`C`開頭）|

排程預設每天 **UTC 00:00（台灣時間早上 8:00）** 自動執行。
如需調整，編輯 `.github/workflows/daily.yml` 的 `cron` 欄位。

也可以在 GitHub Actions 頁面點 **Run workflow** 手動觸發。

## 成本估算

| 項目 | 費用 |
|---|---|
| GitHub Actions | 免費（公開 repo 無限制；私有 repo 每月 2,000 分鐘免費）|
| Gemini 2.5 Flash | ~$0.005–0.01 / 次（約每月 $0.15–0.30 USD）|
| LINE Messaging API | 免費（每月 200 則免費推播，遠超每日一則需求）|
| **每月總計** | **< $0.30 USD** |
