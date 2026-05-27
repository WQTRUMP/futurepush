from __future__ import annotations

import requests


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, timeout_seconds: int = 15, dry_run: bool = False):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds
        self.dry_run = dry_run

    def send_message(self, text: str) -> None:
        if self.dry_run:
            return
        if not self.bot_token or not self.chat_id:
            raise TelegramError("缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = requests.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=self.timeout_seconds,
        )
        if not response.ok:
            raise TelegramError(f"Telegram 推送失败: HTTP {response.status_code} {response.text[:200]}")
