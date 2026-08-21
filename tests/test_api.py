from fastapi.testclient import TestClient

from stock_monitor.api import create_app


def test_health_and_validation(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    assert client.get("/health").json()["mode"] == "read-only-monitor"
    invalid = {
        "symbol": "000001.SZ",
        "timestamp": "2026-08-12T10:00:00+08:00",
        "trading_date": "2026-08-12",
        "open": 10,
        "high": 9,
        "low": 11,
        "close": 10,
        "volume": 100,
        "is_closed": True,
    }
    assert client.post("/bars", json=invalid).status_code == 422


def test_ingest_evaluate_and_dashboard(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    assert client.post("/watchlist", json={"symbol": "000001.SZ", "name": "平安银行"}).status_code == 201
    assert client.post("/reference-closes", json={"symbol": "000001.SZ", "trading_date": "2026-08-12", "previous_close": 10.0}).status_code == 201
    bar = {
        "symbol": "000001.SZ",
        "timestamp": "2026-08-12T10:00:00+08:00",
        "trading_date": "2026-08-12",
        "open": 10.5,
        "high": 10.7,
        "low": 10.4,
        "close": 10.6,
        "volume": 100,
        "is_closed": True,
    }
    assert client.post("/bars", json=bar).status_code == 201
    response = client.post("/evaluate/000001.SZ")
    assert response.status_code == 200
    payload = response.json()
    assert any(item["rule_id"] == "daily_change_up" for item in payload["alerts"])
    assert "qfq 价格序列不完整" in payload["skipped_rules"]["ma_cross"]
    repeated = client.post("/evaluate/000001.SZ").json()
    assert repeated["alerts"] == []
    assert repeated["diagnostics"]["suppressed_alerts"] == 1
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "平安银行" in dashboard.text


def test_research_signal_endpoint_is_read_only_and_explainable(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/watchlist", json={"symbol": "000001.SZ", "name": "平安银行"})
    response = client.get("/research-signals")
    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["symbol"] == "000001.SZ"
    assert payload["state"] == "中性等待"
    assert payload["uncertainties"]
    assert "不构成投资建议" in payload["disclaimer"]
