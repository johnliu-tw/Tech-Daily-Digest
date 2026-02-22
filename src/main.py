"""
main.py - 主程式入口
執行流程: 載入設定 → 抓取文章 → Claude 分析 → LINE 傳送
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 將 src 加入路徑（支援直接執行與 GitHub Actions）
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from crawler import crawl_all
from analyzer import analyze
from notifier import send_to_line

# ── 日誌設定 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# 本機開發：自動讀取 .env（GitHub Actions 上不存在 .env，靠 Secrets 注入）
load_dotenv(ROOT / ".env")


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_settings() -> dict:
    """
    設定優先順序：
    1. 環境變數（GitHub Actions Secrets）
    2. config/settings.json（本機開發用）
    """
    settings_path = ROOT / "config" / "settings.json"

    if settings_path.exists():
        settings = load_json(settings_path)
        logger.info("從 config/settings.json 載入設定")
    else:
        settings = {
            "gemini": {},
            "line": {},
            "crawler": {},
            "output": {},
        }
        logger.info("從環境變數載入設定")

    # 環境變數覆寫（GitHub Actions Secrets）
    env_overrides = {
        ("gemini", "api_key"): "GEMINI_API_KEY",
        ("gemini", "model"):   "GEMINI_MODEL",
        ("line", "channel_access_token"): "LINE_CHANNEL_ACCESS_TOKEN",
        ("line", "target_id"):   "LINE_TARGET_ID",
        ("line", "target_type"): "LINE_TARGET_TYPE",
    }

    for (section, key), env_var in env_overrides.items():
        val = os.environ.get(env_var)
        if val:
            settings.setdefault(section, {})[key] = val

    # 預設值
    settings.setdefault("gemini", {}).setdefault("model", "gemini-2.0-flash")
    settings.setdefault("crawler", {}).setdefault("lookback_hours", 24)
    settings.setdefault("crawler", {}).setdefault("max_articles_per_source", 5)
    settings.setdefault("crawler", {}).setdefault("max_content_chars", 500)
    settings.setdefault("crawler", {}).setdefault("request_timeout", 15)
    settings.setdefault("crawler", {}).setdefault("user_agent", "TechCrawlerBot/1.0")
    settings.setdefault("output", {}).setdefault("top_events", 5)

    return settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="跳過 LINE 傳送，僅印出分析結果（測試用）")
    parser.add_argument("--crawl-only", action="store_true",
                        help="只執行爬蟲，不呼叫 Claude（檢查來源用）")
    args = parser.parse_args()

    logger.info("=== Tech Daily Digest 啟動 ===")

    # 1. 載入設定
    settings = load_settings()
    sources = load_json(ROOT / "config" / "sources.json")

    # 2. 抓取文章
    logger.info("步驟 1/3：抓取文章...")
    articles = crawl_all(sources, settings)

    if not articles:
        logger.warning("沒有抓到任何文章，結束執行")
        sys.exit(0)

    if args.crawl_only:
        print(f"\n{'='*60}")
        print(f"爬蟲結果：共 {len(articles)} 篇文章")
        print('='*60)
        for a in articles:
            print(f"[{a['source']}] {a['title']}")
            print(f"  時間: {a['published_at']}  URL: {a['url']}")
            if a.get('summary'):
                print(f"  摘要: {a['summary'][:100]}...")
            print()
        sys.exit(0)

    # 3. Gemini 分析
    logger.info("步驟 2/3：Gemini 分析中...")
    try:
        events = analyze(articles, settings)
    except Exception as e:
        logger.error(f"分析失敗: {e}")
        sys.exit(1)

    if not events:
        logger.warning("Gemini 未回傳有效事件，結束執行")
        sys.exit(0)

    # 4. 印出分析結果
    print(f"\n{'='*60}")
    print("Gemini 分析結果 — CTO 必看 5 大事件")
    print('='*60)
    for e in events:
        print(f"\n#{e.get('rank','?')} [{e.get('category','')}] {e.get('title','')}")
        print(f"   {e.get('summary','')}")
        print(f"   來源: {e.get('source','')}  |  {e.get('url','')}")
    print()

    if args.dry_run:
        logger.info("=== Dry-run 完成，跳過 LINE 傳送 ===")
        sys.exit(0)

    # 5. 傳送 LINE 訊息
    logger.info(f"步驟 3/3：傳送 {len(events)} 個事件到 LINE")
    success = send_to_line(events, articles, settings)

    if success:
        logger.info("=== 執行完成 ✓ ===")
    else:
        logger.error("LINE 傳送失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
