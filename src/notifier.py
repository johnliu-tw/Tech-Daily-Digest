"""
notifier.py - 透過 LINE Messaging API 傳送摘要
支援：私人訊息（pushMessage）與群組訊息
"""

import requests
import logging
from collections import Counter
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def _append_section(lines: list[str], heading: str, items: list[dict]) -> None:
    """把一個區塊（標題 + 事件列表）追加到訊息行陣列。items 為空時整段略過。"""
    if not items:
        return
    lines.append(f"\n◆ {heading}")
    for i, event in enumerate(items):
        category = event.get("category", "")
        title    = event.get("title", "N/A")
        summary  = event.get("summary", "")
        url      = event.get("url", "")
        source   = event.get("source", "")

        lines.append(f"\n{i+1}. [{category}] {title}")
        if summary:
            lines.append(f"{summary}")
        if url:
            lines.append(f"→ {url}")
        if source:
            lines.append(f"via {source}")


def _format_message(events: dict, articles: list[dict]) -> str:
    """
    將兩區塊事件（main + beginner）格式化成 LINE 純文字訊息
    LINE 單則訊息上限 5000 字元
    """
    tw_time = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"Tech Daily Digest  {tw_time}",
        "─" * 22,
    ]

    main_items     = events.get("main", []) or []
    beginner_items = events.get("beginner", []) or []

    _append_section(lines, "今日精選", main_items)

    if beginner_items:
        lines.append("\n" + "─" * 22)
        _append_section(lines, "新手友善（0-4 年工程師）", beginner_items)

    # ── 統計 footer ──────────────────────────────────────
    lines.append("\n" + "─" * 22)

    source_counts = Counter(a["source"] for a in articles)
    total = sum(source_counts.values())
    active_sources = sorted(
        [(src, cnt) for src, cnt in source_counts.items() if cnt > 0],
        key=lambda x: -x[1]
    )

    lines.append(f"本次分析：{len(source_counts)} 個來源 / {total} 篇文章")
    # 列出有抓到文章的來源
    src_parts = [f"{src}({cnt})" for src, cnt in active_sources]
    if src_parts:
        # 分行避免太長
        chunk, row = [], []
        for part in src_parts:
            row.append(part)
            if len("  ".join(row)) > 36:
                chunk.append("  ".join(row[:-1]))
                row = [part]
        if row:
            chunk.append("  ".join(row))
        lines.extend(chunk)

    return "\n".join(lines)


def send_to_line(events: dict, articles: list[dict], settings: dict) -> bool:
    """
    透過 LINE Messaging API 推送訊息
    articles: 本次所有抓取文章（用於統計 footer）
    回傳 True 代表成功
    """
    line_cfg  = settings.get("line", {})
    token     = line_cfg.get("channel_access_token", "")
    target_id = line_cfg.get("target_id", "")

    if not token or not target_id:
        logger.error("LINE 設定不完整：缺少 channel_access_token 或 target_id")
        return False

    message_text = _format_message(events, articles)
    logger.info(f"LINE 訊息長度: {len(message_text)} 字元")

    # LINE 單則訊息上限 5000 字元
    if len(message_text) > 4999:
        message_text = message_text[:4996] + "..."

    payload = {
        "to": target_id,
        "messages": [{"type": "text", "text": message_text}],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(LINE_PUSH_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        logger.info("LINE 訊息傳送成功")
        return True
    except requests.HTTPError as e:
        logger.error(f"LINE API 錯誤: {e.response.status_code} — {e.response.text}")
        return False
    except requests.RequestException as e:
        logger.error(f"LINE 傳送失敗: {e}")
        return False
