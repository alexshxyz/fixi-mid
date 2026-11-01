import threading

from app import signal_pipeline
from app.match_types import MatchSignal, MarketType, OddsSide


def signal(
    *,
    market: MarketType = "ov",
    strategy: str = "default",
    odds_side: OddsSide | None = None,
) -> MatchSignal:
    return MatchSignal(
        match_id="match-1",
        market=market,
        entry={
            "match_time": "30",
            "ov": {"over": "2.5", "over_odds": 1.55},
            "ah": {
                "ah": "-0.5",
                "home_ah_odds": 1.55,
                "away_ah_odds": 1.55,
            },
            "league": "League",
            "home_team": "Home",
            "away_team": "Away",
            "score": "1-0",
        },
        line="0.5" if market == "ah" else "2.5",
        odds=1.55,
        drop_type="ODDS 1.90 -> 1.55",
        odds_side=odds_side,
        strategy=strategy,
    )


def test_process_match_history_sends_over_and_registers_sent_details(monkeypatch):
    match_signal = signal()
    monkeypatch.setattr(
        signal_pipeline.detector,
        "find_pattern_matches",
        lambda history, shutdown_requested=None: [match_signal],
    )
    sent = []
    registered = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        kwargs["on_sent_details"](
            7, "message", "link", "Over 2.5", "channel"
        )
        return True

    monkeypatch.setattr(signal_pipeline, "send_telegram_notification", fake_send)

    matched_ids = signal_pipeline.process_match_history(
        {"match-1": {}}, registered.append
    )

    assert matched_ids == ["match-1"]
    assert sent[0] == {
        "league": "League",
        "home_team": "Home",
        "away_team": "Away",
        "score": "1-0",
        "over": "2.5",
        "over_odds": 1.55,
        "drop_type": "ODDS 1.90 -> 1.55",
        "match_id": "match-1",
        "match_time": "30",
        "on_sent_details": sent[0]["on_sent_details"],
        "strategy": "default",
        "shutdown_requested": None,
        "settings": None,
    }
    assert registered == [
        {
            "match_id": "match-1",
            "market": "ov",
            "league": "League",
            "message_id": 7,
            "message": "message",
            "link": "link",
            "prediction": "Over 2.5",
            "channel_id": "channel",
        }
    ]


def test_process_match_history_sends_ah_without_tracking_unclosed_signal(
    monkeypatch,
):
    match_signal = signal(
        market="ah",
        strategy="without closed",
        odds_side="home",
    )
    monkeypatch.setattr(
        signal_pipeline.detector,
        "find_pattern_matches",
        lambda history, shutdown_requested=None: [match_signal],
    )
    sent = []
    registered = []
    monkeypatch.setattr(
        signal_pipeline,
        "send_telegram_notification",
        lambda **kwargs: sent.append(kwargs) or False,
    )

    matched_ids = signal_pipeline.process_match_history(
        {"match-1": {}}, registered.append
    )

    assert matched_ids == ["match-1"]
    assert sent[0]["handicap_text"] == "-0.5"
    assert sent[0]["handicap_team_order"] == "Home"
    assert sent[0]["strategy"] == "without closed"
    assert sent[0]["on_sent_details"] is None
    assert registered == []


def test_process_match_history_continues_after_send_error(monkeypatch, caplog):
    signals = [signal(), signal(market="ah", odds_side="away")]
    monkeypatch.setattr(
        signal_pipeline.detector,
        "find_pattern_matches",
        lambda history, shutdown_requested=None: signals,
    )
    attempted = []

    def fail_first_send(**kwargs):
        attempted.append(kwargs)
        if len(attempted) == 1:
            raise RuntimeError("Telegram unavailable")
        return True

    monkeypatch.setattr(
        signal_pipeline, "send_telegram_notification", fail_first_send
    )

    assert signal_pipeline.process_match_history({"match-1": {}}) == [
        "match-1",
        "match-1",
    ]
    assert len(attempted) == 2
    assert "Failed to send notification" in caplog.text


def test_process_match_history_stops_sending_after_shutdown(monkeypatch):
    shutdown_requested = threading.Event()
    signals = [signal(), signal(market="ah", odds_side="away")]
    monkeypatch.setattr(
        signal_pipeline.detector,
        "find_pattern_matches",
        lambda history, shutdown_requested=None: signals,
    )
    sent = []

    def send_and_shutdown(**kwargs):
        sent.append(kwargs)
        shutdown_requested.set()
        return True

    monkeypatch.setattr(
        signal_pipeline, "send_telegram_notification", send_and_shutdown
    )

    matched_ids = signal_pipeline.process_match_history(
        {"match-1": {}},
        shutdown_requested=shutdown_requested,
    )

    assert matched_ids == ["match-1"]
    assert len(sent) == 1
