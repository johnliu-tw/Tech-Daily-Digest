"""
notifier.py - 透過 LINE Messaging API 傳送摘要
支援：私人訊息（pushMessage）與群組訊息
LINE bot 設定說明：https://developers.line.biz/en/docs/messaging-api/
"""

import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def _format_message(events: list[dict]) -> str:
    """
    將 5 大事件格式化成 LINE 純文字訊息
    LINE 單則訊息上限 5000 字元，此格式約 1500 字元
    """
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"📡 Tech Daily Digest — {now}",
        f"🎯 CTO 必看 5 大科技事件",
        "━" * 20,
    ]

    icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

    for i, event in enumerate(events):
        icon = icons[i] if i < len(icons) else f"{i+1}."
        lines.append(f"\n{icon} {event.get('title', 'N/A')}")

        category = event.get("category", "")
        if category:
            lines.append(f"   [{category}]")

        summary = event.get("summary", "")
        if summary:
            lines.append(f"   {summary}")

        url = event.get("url", "")
        if url:
            lines.append(f"   🔗 {url}")

        source = event.get("source", "")
        if source:
            lines.append(f"   📰 來源: {source}")

    lines.append("\n━" * 20)
    lines.append("⚙️ Powered by Claude Haiku + GitHub Actions")

    return "\n".join(lines)


def send_to_line(events: list[dict], settings: dict) -> bool:
    """
    透過 LINE Messaging API 推送訊息
    回傳 True 代表成功
    """
    line_cfg = settings.get("line", {})
    token = line_cfg.get("channel_access_token", "")
    target_id = line_cfg.get("target_id", "")

    if not token or not target_id:
        logger.error("LINE 設定不完整：缺少 channel_access_token 或 target_id")
        return False

    message_text = _format_message(events)

    # LINE 單則訊息上限 5000 字元
    if len(message_text) > 4999:
        message_text = message_text[:4996] + "..."

    payload = {
        "to": target_id,
        "messages": [
            {
                "type": "text",
                "text": message_text,
            }
        ],
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
