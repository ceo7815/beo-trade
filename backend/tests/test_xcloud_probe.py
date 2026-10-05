from app.integrations.probes import probe_xcloud


def test_xcloud_is_connected_when_the_server_database_answers(monkeypatch):
    monkeypatch.setattr("app.models.db.database_ready", lambda: True)
    status, _latency, detail = probe_xcloud()
    assert status == "connected"
    assert "השרת" in detail


def test_xcloud_stays_disconnected_when_the_database_does_not_answer(monkeypatch):
    monkeypatch.setattr("app.models.db.database_ready", lambda: False)
    status, _latency, detail = probe_xcloud()
    assert status == "disconnected"
    assert "לא ענה" in detail
