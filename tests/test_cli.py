import logging
import os
from datetime import datetime
from pathlib import Path

from futures_signal.cli import _SensitiveDataFilter, configure_logging, main
from futures_signal.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        wecom_webhook_url="",
        timezone_name="Asia/Shanghai",
        sample_interval_seconds=60,
        alert_cooldown_seconds=300,
        push_every_sample=False,
        run_outside_market_hours=True,
        use_trade_calendar=False,
        trade_calendar_cache_path=tmp_path / "trade_dates.json",
        fetch_term_structure=False,
        fetch_term_structure_every_seconds=300,
        fetch_position_rank=True,
        position_trend_days=5,
        dividend_season_adjust=True,
        basis_history_days=20,
        roll_window_days=7,
        ai_commentary_enabled=False,
        deepseek_api_key="",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-pro",
        deepseek_timeout_seconds=20,
        deepseek_max_tokens=420,
        deepseek_temperature=0.2,
        deepseek_thinking_enabled=False,
        deepseek_reasoning_effort="high",
        log_level="INFO",
        data_dir=tmp_path,
        db_path=tmp_path / "market.db",
    )


def test_sensitive_data_filter_redacts_query_secrets():
    redacted = _SensitiveDataFilter()._redact(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret&foo=bar"
    )

    assert "key=***" in redacted
    assert "foo=bar" in redacted
    assert "secret" not in redacted


def test_configure_logging_creates_restricted_log_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    for handler in original_handlers:
        root.removeHandler(handler)

    try:
        configure_logging(_settings(tmp_path))
        log_path = tmp_path / "logs" / "futures_signal.log"
        assert log_path.exists()
        assert oct(os.stat(log_path.parent).st_mode & 0o777) == "0o700"
        assert oct(os.stat(log_path).st_mode & 0o777) == "0o600"
    finally:
        for handler in list(root.handlers):
            handler.close()
            root.removeHandler(handler)
        root.setLevel(original_level)
        for handler in original_handlers:
            root.addHandler(handler)


def test_sensitive_data_filter_redacts_record_args():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="webhook=%s",
        args=("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret&token=abc",),
        exc_info=None,
    )

    allowed = _SensitiveDataFilter().filter(record)

    assert allowed is True
    assert record.args == ("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=***&token=***",)


def test_sensitive_data_filter_keeps_plain_text_and_queryless_urls():
    redaction_filter = _SensitiveDataFilter()

    assert redaction_filter._redact("no url here") == "no url here"
    assert redaction_filter._redact("https://example.com/path") == "https://example.com/path"


def test_main_init_db_uses_calendar_aware_storage(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    created = {}

    class FakeCalendar:
        pass

    class FakeStorage:
        def __init__(self, db_path, calendar):
            created["db_path"] = db_path
            created["calendar"] = calendar
            created["inited"] = False

        def init(self):
            created["inited"] = True

    monkeypatch.setattr("futures_signal.cli.Settings.from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr("futures_signal.cli.configure_logging", lambda _settings: None)
    monkeypatch.setattr("futures_signal.cli.setup_runtime_dirs", lambda _settings: None)
    monkeypatch.setattr("futures_signal.cli.TradingCalendar", lambda *args, **kwargs: FakeCalendar())
    monkeypatch.setattr("futures_signal.cli.Storage", FakeStorage)

    main(["init-db"])

    output = capsys.readouterr().out
    assert created["db_path"] == settings.db_path
    assert isinstance(created["calendar"], FakeCalendar)
    assert created["inited"] is True
    assert str(settings.db_path) in output


def test_main_evaluate_runs_prediction_job(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    created = {}

    class FakeCalendar:
        source = "weekday"

    class FakeStorage:
        def __init__(self, db_path, calendar):
            created["db_path"] = db_path
            created["calendar"] = calendar
            self.predictions = object()
            self.prediction_labels = object()

        def init(self):
            created["inited"] = True

    class FakeJob:
        def __init__(self, predictions, prediction_labels):
            created["predictions"] = predictions
            created["prediction_labels"] = prediction_labels

        def run(self, until, limit):
            created["until"] = until
            created["limit"] = limit
            return type(
                "Result",
                (),
                {
                    "evaluated_at": until,
                    "scanned": 7,
                    "labeled": 3,
                    "skipped_not_due": 2,
                    "skipped_missing_samples": 2,
                    "remaining_unlabeled": 4,
                },
            )()

    monkeypatch.setattr("futures_signal.cli.Settings.from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr("futures_signal.cli.configure_logging", lambda _settings: None)
    monkeypatch.setattr("futures_signal.cli.setup_runtime_dirs", lambda _settings: None)
    monkeypatch.setattr("futures_signal.cli.TradingCalendar", lambda *args, **kwargs: FakeCalendar())
    monkeypatch.setattr("futures_signal.cli.Storage", FakeStorage)
    monkeypatch.setattr("futures_signal.cli.PredictionEvaluationJob", FakeJob)

    main(["evaluate", "--until", "2026-06-02T10:30:00+08:00", "--limit", "25"])

    output = capsys.readouterr().out
    assert created["db_path"] == settings.db_path
    assert isinstance(created["calendar"], FakeCalendar)
    assert created["inited"] is True
    assert created["predictions"] is not None
    assert created["prediction_labels"] is not None
    assert created["until"] == datetime.fromisoformat("2026-06-02T10:30:00+08:00")
    assert created["limit"] == 25
    assert "scanned: 7" in output
    assert "remaining_unlabeled: 4" in output
