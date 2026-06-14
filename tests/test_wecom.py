import pytest

from futures_signal.wecom import WeComClient, WeComError


class FakeResponse:
    ok = True
    status_code = 200
    text = "ok"

    def json(self):
        return {"errcode": 0}


def test_wecom_client_splits_long_messages(monkeypatch):
    calls = []

    def fake_post(url, json, timeout, allow_redirects):
        calls.append((url, json, timeout))
        return FakeResponse()

    monkeypatch.setattr("futures_signal.wecom.requests.post", fake_post)

    client = WeComClient("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test")
    client.send_message("A" * 2000 + "\n" + "B" * 1000)

    assert len(calls) == 3
    assert all(call[1]["msgtype"] == "text" for call in calls)
    assert all(len(call[1]["text"]["content"]) <= 1800 for call in calls)


def test_wecom_client_requires_webhook_url():
    client = WeComClient("")

    with pytest.raises(WeComError, match="缺少 WECOM_WEBHOOK_URL"):
        client.send_message("hello")


def test_wecom_client_raises_on_http_error(monkeypatch):
    class ErrorResponse:
        ok = False
        status_code = 502

        def json(self):
            return {"errcode": 0}

    monkeypatch.setattr("futures_signal.wecom.requests.post", lambda *args, **kwargs: ErrorResponse())

    client = WeComClient("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test")

    with pytest.raises(WeComError, match="HTTP 502"):
        client.send_message("hello")


def test_wecom_client_raises_on_invalid_json(monkeypatch):
    class InvalidJsonResponse:
        ok = True
        status_code = 200

        def json(self):
            raise ValueError("bad json")

    monkeypatch.setattr("futures_signal.wecom.requests.post", lambda *args, **kwargs: InvalidJsonResponse())

    client = WeComClient("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test")

    with pytest.raises(WeComError, match="响应不是合法 JSON"):
        client.send_message("hello")


def test_wecom_client_raises_on_nonzero_errcode(monkeypatch):
    class BizErrorResponse:
        ok = True
        status_code = 200

        def json(self):
            return {"errcode": 93000}

    monkeypatch.setattr("futures_signal.wecom.requests.post", lambda *args, **kwargs: BizErrorResponse())

    client = WeComClient("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test")

    with pytest.raises(WeComError, match="errcode=93000"):
        client.send_message("hello")
