CREATE DATABASE IF NOT EXISTS __DATABASE__ KEEP 3650d PRECISION 'ms';

CREATE STABLE IF NOT EXISTS __DATABASE__.market_bars (
    ts TIMESTAMP,
    event_id VARCHAR(64),
    ingest_ts TIMESTAMP,
    trading_date VARCHAR(10),
    open_price DOUBLE,
    high_price DOUBLE,
    low_price DOUBLE,
    close_price DOUBLE,
    qfq_open DOUBLE,
    qfq_high DOUBLE,
    qfq_low DOUBLE,
    qfq_close DOUBLE,
    volume DOUBLE,
    source VARCHAR(64),
    is_closed BOOL
) TAGS (
    symbol VARCHAR(32),
    interval_code VARCHAR(16)
);

CREATE STABLE IF NOT EXISTS __DATABASE__.market_activity_1m (
    ts TIMESTAMP,
    window_end TIMESTAMP,
    event_count BIGINT,
    high_price DOUBLE,
    low_price DOUBLE,
    average_close DOUBLE,
    total_volume DOUBLE
) TAGS (
    symbol VARCHAR(32),
    interval_code VARCHAR(16)
);

CREATE STABLE IF NOT EXISTS __DATABASE__.fund_flows (
    ts TIMESTAMP,
    event_id VARCHAR(64),
    ingest_ts TIMESTAMP,
    trading_date VARCHAR(10),
    entity_name VARCHAR(100),
    latest_price DOUBLE,
    change_pct DOUBLE,
    main_net_inflow DOUBLE,
    main_net_ratio DOUBLE,
    super_large_net DOUBLE,
    large_net DOUBLE,
    medium_net DOUBLE,
    small_net DOUBLE,
    source VARCHAR(64),
    is_degraded BOOL
) TAGS (
    entity_type VARCHAR(16),
    entity_code VARCHAR(64)
);

CREATE STABLE IF NOT EXISTS __DATABASE__.news_sentiment_1m (
    ts TIMESTAMP,
    window_end TIMESTAMP,
    event_count BIGINT,
    positive_count BIGINT,
    negative_count BIGINT,
    neutral_count BIGINT,
    average_score DOUBLE
) TAGS (
    source VARCHAR(64)
);
