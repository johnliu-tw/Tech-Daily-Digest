# 🤖 Tech Daily Digest

每日自動抓取科技新聞，用 CTO 視角透過 Claude AI 分析出 5 大必讀事件，傳送到 LINE。

## 架構

```
每日 GitHub Actions 觸發
  → crawler.py 抓取 RSS feeds（過去 24 小時）
  → analyzer.py 呼叫 Claude Haiku API（單次 API call）
  → notifier.py 傳送到 LINE
```

## 快速開始

### 1. 設定 RSS 來源

編輯 `config/sources.json`，新增或移除要追蹤的 RSS feed URL。

### 2. 取得必要 API 金鑰

#### Claude API Key
1. 前往 [console.anthropic.com](https://console.anthropic.com)
2. 建立 API Key（注意：這是獨立的 API 費用帳戶，不是 Claude.ai 訂閱）
3. 預估費用：每次執行 < $0.01 USD（使用 Haiku 模型）

#### LINE Messaging API

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立一個 **Provider** → **Messaging API channel**
3. 取得 **Channel Access Token**（長期 token）

**取得你的 LINE User ID 或 Group ID：**

- **個人訊息**：在 LINE Developers Console → Basic settings 頁面找到你的 `userId`
  - 或：把 bot 加為好友，傳任意訊息，在 webhook 事件中取得 userId
  - 也可以用 [Line ID Finder Bot](https://line.me/R/ti/p/%40lineid) 等工具

- **群組訊息**：
  1. 把 bot 加入群組
  2. 任何人在群組傳訊息時，webhook 會收到 `groupId`
  3. 在 Channel settings 開啟 webhook，設定 webhook URL（可用 ngrok 本機測試）
  4. 記錄 `groupId`

### 3. 本機測試

```bash
# 複製設定檔範本
cp config/settings.example.json config/settings.json

# 編輯填入你的 API 金鑰
vim config/settings.json

# 安裝依賴
pip install -r requirements.txt

# 執行
python src/main.py
```

### 4. 部署到 GitHub Actions

在你的 GitHub Repo → Settings → Secrets and variables → Actions，新增以下 Secrets：

| Secret 名稱 | 說明 |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API 金鑰 |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE channel access token |
| `LINE_TARGET_ID` | LINE 使用者 ID 或群組 ID |
| `LINE_TARGET_TYPE` | `user` 或 `group` |

預設每天 UTC 00:00（台灣時間早上 8:00）執行。
修改排程請編輯 `.github/workflows/daily.yml` 中的 `cron` 設定。

## 自訂設定

### 調整爬取來源

編輯 `config/sources.json`：
```json
{
  "rss_feeds": [
    {
      "name": "顯示名稱",
      "url": "https://example.com/feed.xml",
      "category": "tech"
    }
  ]
}
```

### 調整分析行為

在 `config/settings.json` 中可調整：
- `crawler.lookback_hours`：抓取幾小時內的文章（預設 24）
- `crawler.max_articles_per_source`：每個來源最多幾篇（預設 5）
- `output.top_events`：最終顯示幾個事件（預設 5）

## 成本估算

| 項目 | 費用 |
|---|---|
| GitHub Actions | 免費（公開 repo 無限制，私有 repo 每月 2000 分鐘免費）|
| Claude Haiku API | ~$0.003–0.008 / 次 |
| LINE Messaging API | 免費（每月 200 則免費推播） |
| **每月總計** | **< $0.25 USD** |
# Tech-Daily-Digest
