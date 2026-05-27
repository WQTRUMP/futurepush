from futures_signal.wecom import WeComClient


class FakeResponse:
    ok = True
    status_code = 200
    text = "ok"

    def json(self):
        return {"errcode": 0}


def test_wecom_client_splits_long_messages(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse()

    monkeypatch.setattr("futures_signal.wecom.requests.post", fake_post)

    client = WeComClient("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test")
    client.send_message("A" * 2000 + "\n" + "B" * 1000)

    assert len(calls) == 3
    assert all(call[1]["msgtype"] == "text" for call in calls)
    assert all(len(call[1]["text"]["content"]) <= 1800 for call in calls)
