"""
analyzer.py - 使用 Gemini API 分析文章，同時選出「今日精選」與「新手友善」兩區塊
成本優化：
  - 單次 API 呼叫同時產出兩個區塊
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
PROMPT_TEMPLATE = """你是一位技術新聞編輯，需要為兩種不同讀者整理每日科技摘要。
以下是過去 24 小時內從各科技媒體抓取的文章列表（JSON 格式）。
注意：部分文章的 published_at 標記為 "unknown"，請根據內容判斷是否為近期事件。

請產出兩個區塊：

【區塊 A — 今日精選 {top_n} 則】
目標讀者：資深技術人員 / 技術決策者
從所有文章中選出影響最廣、最值得關注的 {top_n} 則事件，優先選擇：
- AI / ML 重大進展或產品發布
- 雲端、平台、開發工具的重要更新
- 資安漏洞或重大事件
- 影響開發生態的開源動態

【區塊 B — 新手友善 最多 {beginner_n} 則】
目標讀者：0-4 年經驗工程師
從剩餘文章（不得與區塊 A 重複）中選出最多 {beginner_n} 則，優先四大方向：
1. 基礎觀念 / 工具教學：入門級技術文章、框架或工具的 how-to、最佳實踐
2. 熱門技術的淺顯解讀：把 AI、Cloud 等當紅主題用初階可理解的角度介紹
3. 開源專案 / 新工具介紹：實用 side-project、小工具、生產力類分享
4. 職涯 / 學習導向：學習資源、職涯成長、轉職、面試、軟技能
排除：
- 過於 niche 或需要深厚背景的研究性 paper
- 純產業新聞、併購、融資消息
- 技術決策層級的深度分析（那些屬於區塊 A）

**寧缺勿濫**：若當日找不到 {beginner_n} 篇真正適合初階工程師的文章，
回少於 {beginner_n} 篇（甚至空陣列 []）也可以，請勿硬湊。

摘要寫作原則（兩區塊皆適用）：
- 直接說明「發生了什麼」和「為什麼重要」
- 客觀、精簡，不加說教或建議行動
- 100 字以內，繁體中文

輸出為 JSON object，格式：
{{
  "main": [
    {{
      "rank": 1,
      "title": "事件標題（繁體中文，英文標題請翻譯）",
      "summary": "摘要（100 字內，繁體中文）",
      "url": "原文連結（保持原始 URL，不要修改）",
      "source": "來源媒體名稱",
      "category": "AI / Cloud / Security / DevTools / Open Source / Platform / Other"
    }}
  ],
  "beginner": [
    {{
      "rank": 1,
      "title": "...",
      "summary": "...",
      "url": "...",
      "source": "...",
      "category": "..."
    }}
  ]
}}

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


def analyze(articles: list[dict], settings: dict) -> dict:
    """
    呼叫 Gemini API，回傳 {"main": [...], "beginner": [...]} 兩區塊結構
    """
    if not articles:
        logger.warning("沒有文章可分析")
        return {"main": [], "beginner": []}

    gemini_cfg = settings.get("gemini", {})
    api_key    = gemini_cfg.get("api_key", "")
    model      = gemini_cfg.get("model", "gemini-2.0-flash")
    output_cfg = settings.get("output", {})
    top_n      = output_cfg.get("top_events", 7)
    beginner_n = output_cfg.get("top_beginner_events", 3)

    client = genai.Client(api_key=api_key)

    articles_json = _build_articles_payload(articles)
    prompt        = PROMPT_TEMPLATE.format(
        articles_json=articles_json,
        top_n=top_n,
        beginner_n=beginner_n,
    )

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
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError(f"回應不是 JSON object，收到: {type(result)}")
        if "main" not in result or "beginner" not in result:
            raise ValueError(f"回應缺少 main 或 beginner 欄位，實際 keys: {list(result.keys())}")
        if not isinstance(result["main"], list) or not isinstance(result["beginner"], list):
            raise ValueError("main / beginner 必須是陣列")
        result["main"]     = result["main"][:top_n]
        result["beginner"] = result["beginner"][:beginner_n]
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
    logger.info(f"選出 main={len(result['main'])} 則、beginner={len(result['beginner'])} 則")

    return result
