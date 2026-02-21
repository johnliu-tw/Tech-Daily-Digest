"""
analyzer.py - 使用 Gemini API 分析文章，從 CTO 角度選出 5 大事件
成本優化：
  - 單次 API 呼叫處理所有文章
  - 使用 Gemini 2.0 Flash（免費額度：每天 1,500 次，遠超每日一次需求）
  - response_mime_type="application/json" 強制 JSON 輸出，省去解析錯誤重試成本
"""

import json
import logging
import time

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Prompt 不需要再叮嚀「輸出合法 JSON、不要加 code block」
# 因為 response_mime_type 會強制保證輸出格式
PROMPT_TEMPLATE = """你是一位技術新聞編輯，專門為忙碌的技術人員整理每日科技摘要。
以下是過去 24 小時內從各科技媒體抓取的文章列表（JSON 格式）。
注意：部分文章的 published_at 標記為 "unknown"，請根據內容判斷是否為近期事件。

從中選出影響最廣、最值得關注的 **5 則事件**，優先選擇：
- AI / ML 重大進展或產品發布
- 雲端、平台、開發工具的重要更新
- 資安漏洞或重大事件
- 影響開發生態的開源動態

摘要寫作原則：
- 直接說明「發生了什麼」和「為什麼重要」
- 客觀、精簡，不加說教或建議行動
- 100 字以內，繁體中文

輸出為 JSON 陣列，每個物件欄位：
- rank: 排名（整數 1–5）
- title: 事件標題（繁體中文，英文標題請翻譯）
- summary: 摘要（100 字內，繁體中文）
- url: 原文連結（保持原始 URL，不要修改）
- source: 來源媒體名稱
- category: 分類（AI / Cloud / Security / DevTools / Open Source / Platform / Other）

以下是文章資料：
{articles_json}
"""


def _build_articles_payload(articles: list[dict]) -> str:
    """將文章精簡化後序列化，只保留分析所需欄位以減少 token"""
    slim = [
        {
            "title":        a.get("title", ""),
            "source":       a.get("source", ""),
            "url":          a.get("url", ""),
            "published_at": a.get("published_at", "unknown"),
            "summary":      a.get("summary", "")[:400],
        }
        for a in articles
    ]
    return json.dumps(slim, ensure_ascii=False)


def analyze(articles: list[dict], settings: dict) -> list[dict]:
    """
    呼叫 Gemini API，回傳 5 個精選事件列表
    """
    if not articles:
        logger.warning("沒有文章可分析")
        return []

    gemini_cfg = settings.get("gemini", {})
    api_key    = gemini_cfg.get("api_key", "")
    model      = gemini_cfg.get("model", "gemini-2.0-flash")
    top_n      = settings.get("output", {}).get("top_events", 5)

    client = genai.Client(api_key=api_key)

    articles_json = _build_articles_payload(articles)
    prompt        = PROMPT_TEMPLATE.format(articles_json=articles_json)

    logger.info(f"呼叫 Gemini API（model={model}，文章數={len(articles)}）")

    # 503 過載時自動 retry（preview model 常見）
    last_err = None
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=8192,
                    temperature=1.0,
                ),
            )
            last_err = None
            break
        except Exception as e:
            last_err = e
            if "503" in str(e) and attempt < 3:
                wait = attempt * 15
                logger.warning(f"Gemini 503 過載，{wait} 秒後重試（第 {attempt}/3 次）...")
                time.sleep(wait)
            else:
                logger.error(f"Gemini API 呼叫失敗: {e}")
                raise

    if last_err:
        raise last_err

    # thinking model 的回應會帶有 thought_signature parts，
    # 只取 text parts 的串接結果
    raw = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            raw += part.text

    try:
        events = json.loads(raw)
        if not isinstance(events, list):
            raise ValueError(f"回應不是 JSON 陣列，收到: {type(events)}")
        events = events[:top_n]
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"JSON 解析失敗: {e}\n原始回應:\n{raw}")
        raise

    # 記錄 token 用量
    usage = response.usage_metadata
    logger.info(
        f"Token 用量 — 輸入: {usage.prompt_token_count}, "
        f"輸出: {usage.candidates_token_count}, "
        f"合計: {usage.total_token_count}"
    )

    return events
