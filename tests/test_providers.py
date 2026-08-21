from stock_monitor.providers import CSVReplayProvider


def test_csv_replay_is_sorted_and_validated(tmp_path):
    bars = tmp_path / "bars.csv"
    bars.write_text(
        "symbol,interval,timestamp,trading_date,open,high,low,close,qfq_open,qfq_high,qfq_low,qfq_close,volume,is_closed,source\n"
        "000001.SZ,1m,2026-08-12T10:01:00+08:00,2026-08-12,10,11,9,10.2,10,11,9,10.2,100,true,test\n"
        "000001.SZ,1m,2026-08-12T10:00:00+08:00,2026-08-12,10,11,9,10.1,10,11,9,10.1,100,true,test\n",
        encoding="utf-8",
    )
    provider = CSVReplayProvider(bars)
    replayed = list(provider.bars("000001.SZ", "1m"))
    assert len(replayed) == 2
    assert replayed[0].timestamp < replayed[1].timestamp
    assert replayed[0].is_closed is True


def test_csv_blank_optional_qfq_values_become_none(tmp_path):
    bars = tmp_path / "bars.csv"
    bars.write_text(
        "symbol,interval,timestamp,trading_date,open,high,low,close,qfq_open,qfq_high,qfq_low,qfq_close,volume,is_closed,source\n"
        "000001.SZ,1m,2026-08-12T10:00:00+08:00,2026-08-12,10,11,9,10.1,,,,,100,true,test\n",
        encoding="utf-8",
    )
    replayed = list(CSVReplayProvider(bars).bars("000001.SZ", "1m"))
    assert replayed[0].qfq_close is None
