from stock_monitor.config import ConfigStore, DesktopConfig


def test_config_round_trip_excludes_secrets(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    config = DesktopConfig(provider="tushare", sync_days=365, cls_api_base_url="https://licensed.example")
    store.save(config)
    loaded = store.load()
    assert loaded.provider == "tushare"
    assert loaded.sync_days == 365
    text = path.read_text(encoding="utf-8")
    assert "token" not in text.lower()


def test_advanced_config_round_trip_does_not_store_password(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    config = DesktopConfig(
        advanced_mode=True,
        kafka_bootstrap_servers="localhost:19092",
        tdengine_database="ashare_test",
        flink_dashboard_url="http://localhost:8081",
    )
    store.save(config)

    loaded = store.load()
    assert loaded.advanced_mode is True
    assert loaded.tdengine_database == "ashare_test"
    assert "password" not in path.read_text(encoding="utf-8").lower()
