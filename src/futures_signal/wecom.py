from __future__ import annotations

import requests

MAX_TEXT_CHARS = 1800


class WeComError(RuntimeError):
    pass


class WeComClient:
    def __init__(self, webhook_url: str, timeout_seconds: int = 15, dry_run: bool = False):
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds
        self.dry_run = dry_run

    def send_message(self, text: str) -> None:
        if self.dry_run:
            return
        if not self.webhook_url:
            raise WeComError("缺少 WECOM_WEBHOOK_URL")

        for chunk in _split_text(text):
            self._send_text(chunk)

    def _send_text(self, text: str) -> None:
        response = requests.post(
            self.webhook_url,
            json={
                "msgtype": "text",
                "text": {
                    "content": text,
                },
            },
            timeout=self.timeout_seconds,
            allow_redirects=False,
        )
        if not response.ok:
            raise WeComError(f"企业微信推送失败: HTTP {response.status_code}")

        try:
            result = response.json()
        except ValueError as exc:
            raise WeComError("企业微信推送失败: 响应不是合法 JSON") from exc
        if result.get("errcode") != 0:
            raise WeComError(f"企业微信推送失败: errcode={result.get('errcode')}")


def _split_text(text: str) -> list[str]:
    if len(text) <= MAX_TEXT_CHARS:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(line) > MAX_TEXT_CHARS:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.extend(line[i : i + MAX_TEXT_CHARS].rstrip() for i in range(0, len(line), MAX_TEXT_CHARS))
            continue

        if len(current) + len(line) > MAX_TEXT_CHARS:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line

    if current:
        chunks.append(current.rstrip())
    return [chunk for chunk in chunks if chunk]
