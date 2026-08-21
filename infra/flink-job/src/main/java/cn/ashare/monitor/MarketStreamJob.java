package cn.ashare.monitor;

import org.apache.flink.api.common.restartstrategy.RestartStrategies;
import org.apache.flink.streaming.api.CheckpointingMode;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.EnvironmentSettings;
import org.apache.flink.table.api.StatementSet;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;

import java.time.Duration;

/**
 * Canonical A-share stream pipeline.
 *
 * Kafka is the replay boundary, Flink performs event-time deduplication and
 * one-minute windows, and the official TDengine connector persists time-series
 * results with at-least-once checkpoint semantics.
 */
public final class MarketStreamJob {
    private MarketStreamJob() {}

    public static void main(String[] args) {
        String brokers = env("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092");
        String marketTopic = env("KAFKA_MARKET_TOPIC", "ashare.market.bar.v1");
        String flowTopic = env("KAFKA_FUND_FLOW_TOPIC", "ashare.fund-flow.v1");
        String newsTopic = env("KAFKA_NEWS_TOPIC", "ashare.news.v1");
        String database = identifier(env("TDENGINE_DATABASE", "ashare"));
        String tdengineUrl = env(
                "TDENGINE_JDBC_URL",
                "jdbc:TAOS-WS://tdengine:6041/" + database + "?user=root&password=taosdata"
        );

        StreamExecutionEnvironment execution = StreamExecutionEnvironment.getExecutionEnvironment();
        execution.enableCheckpointing(5_000L, CheckpointingMode.AT_LEAST_ONCE);
        execution.getCheckpointConfig().setCheckpointTimeout(60_000L);
        execution.getCheckpointConfig().setMinPauseBetweenCheckpoints(1_000L);
        execution.setRestartStrategy(RestartStrategies.failureRateRestart(
                3,
                org.apache.flink.api.common.time.Time.minutes(5),
                org.apache.flink.api.common.time.Time.seconds(10)
        ));

        StreamTableEnvironment tables = StreamTableEnvironment.create(
                execution,
                EnvironmentSettings.newInstance().inStreamingMode().build()
        );
        tables.getConfig().setIdleStateRetention(Duration.ofHours(24));
        tables.getConfig().getConfiguration().setString("table.exec.source.idle-timeout", "30 s");

        createMarketTables(tables, brokers, marketTopic, tdengineUrl, database);
        createFlowTables(tables, brokers, flowTopic, tdengineUrl, database);
        createNewsTables(tables, brokers, newsTopic, tdengineUrl, database);

        StatementSet statements = tables.createStatementSet();
        statements.addInsertSql("""
                INSERT INTO td_market_bars
                SELECT event_ts,event_id,TO_TIMESTAMP_LTZ(ingest_time_ms,3),trading_date,
                       open_price,high_price,low_price,close_price,
                       qfq_open,qfq_high,qfq_low,qfq_close,volume,source,is_closed,
                       symbol,interval_code,bar_tbname
                FROM market_dedup
                """);
        statements.addInsertSql("""
                INSERT INTO td_market_activity
                SELECT window_start,window_end,COUNT(*),MAX(high_price),MIN(low_price),
                       AVG(close_price),SUM(volume),symbol,interval_code,activity_tbname
                FROM TABLE(
                    TUMBLE(TABLE market_dedup, DESCRIPTOR(event_ts), INTERVAL '1' MINUTE)
                )
                GROUP BY window_start,window_end,symbol,interval_code,activity_tbname
                """);
        statements.addInsertSql("""
                INSERT INTO td_fund_flows
                SELECT event_ts,event_id,TO_TIMESTAMP_LTZ(ingest_time_ms,3),trading_date,
                       entity_name,latest_price,change_pct,main_net_inflow,main_net_ratio,
                       super_large_net,large_net,medium_net,small_net,source,is_degraded,
                       entity_type,entity_code,flow_tbname
                FROM flow_dedup
                """);
        statements.addInsertSql("""
                INSERT INTO td_news_sentiment
                SELECT window_start,window_end,COUNT(*),
                       SUM(CASE WHEN sentiment='利好' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN sentiment='利空' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN sentiment='中性' THEN 1 ELSE 0 END),
                       AVG(sentiment_score),source,news_tbname
                FROM TABLE(
                    TUMBLE(TABLE news_dedup, DESCRIPTOR(event_ts), INTERVAL '1' MINUTE)
                )
                GROUP BY window_start,window_end,source,news_tbname
                """);
        statements.execute();
    }

    private static void createMarketTables(
            StreamTableEnvironment t, String brokers, String topic, String jdbcUrl, String database
    ) {
        t.executeSql("""
                CREATE TABLE market_source (
                    event_id STRING,
                    event_time_ms BIGINT,
                    ingest_time_ms BIGINT,
                    symbol STRING,
                    interval_code STRING,
                    trading_date STRING,
                    open_price DOUBLE,
                    high_price DOUBLE,
                    low_price DOUBLE,
                    close_price DOUBLE,
                    qfq_open DOUBLE,
                    qfq_high DOUBLE,
                    qfq_low DOUBLE,
                    qfq_close DOUBLE,
                    volume DOUBLE,
                    is_closed BOOLEAN,
                    source STRING,
                    bar_tbname STRING,
                    activity_tbname STRING,
                    event_ts AS TO_TIMESTAMP_LTZ(event_time_ms,3),
                    WATERMARK FOR event_ts AS event_ts - INTERVAL '5' SECOND
                ) WITH (
                    'connector'='kafka',
                    'topic'='%s',
                    'properties.bootstrap.servers'='%s',
                    'properties.group.id'='ashare-flink-market-v1',
                    'scan.startup.mode'='group-offsets',
                    'format'='json',
                    'json.ignore-parse-errors'='false'
                )
                """.formatted(sql(topic), sql(brokers)));
        t.executeSql("""
                CREATE TEMPORARY VIEW market_dedup AS
                SELECT * FROM (
                    SELECT *,ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY ingest_time_ms DESC) AS row_num
                    FROM market_source
                ) WHERE row_num=1
                """);
        t.executeSql(tdSink(
                "td_market_bars",
                """
                    ts TIMESTAMP(3), event_id STRING, ingest_ts TIMESTAMP(3), trading_date STRING,
                    open_price DOUBLE, high_price DOUBLE, low_price DOUBLE, close_price DOUBLE,
                    qfq_open DOUBLE, qfq_high DOUBLE, qfq_low DOUBLE, qfq_close DOUBLE,
                    volume DOUBLE, source STRING, is_closed BOOLEAN,
                    symbol STRING, interval_code STRING, tbname STRING
                """,
                jdbcUrl, database, "market_bars"
        ));
        t.executeSql(tdSink(
                "td_market_activity",
                """
                    ts TIMESTAMP(3), window_end TIMESTAMP(3), event_count BIGINT,
                    high_price DOUBLE, low_price DOUBLE, average_close DOUBLE, total_volume DOUBLE,
                    symbol STRING, interval_code STRING, tbname STRING
                """,
                jdbcUrl, database, "market_activity_1m"
        ));
    }

    private static void createFlowTables(
            StreamTableEnvironment t, String brokers, String topic, String jdbcUrl, String database
    ) {
        t.executeSql("""
                CREATE TABLE flow_source (
                    event_id STRING, event_time_ms BIGINT, ingest_time_ms BIGINT,
                    entity_type STRING, entity_code STRING, entity_name STRING, trading_date STRING,
                    latest_price DOUBLE, change_pct DOUBLE, main_net_inflow DOUBLE, main_net_ratio DOUBLE,
                    super_large_net DOUBLE, large_net DOUBLE, medium_net DOUBLE, small_net DOUBLE,
                    source STRING, is_degraded BOOLEAN, flow_tbname STRING,
                    event_ts AS TO_TIMESTAMP_LTZ(event_time_ms,3),
                    WATERMARK FOR event_ts AS event_ts - INTERVAL '5' SECOND
                ) WITH (
                    'connector'='kafka','topic'='%s','properties.bootstrap.servers'='%s',
                    'properties.group.id'='ashare-flink-flow-v1','scan.startup.mode'='group-offsets',
                    'format'='json','json.ignore-parse-errors'='false'
                )
                """.formatted(sql(topic), sql(brokers)));
        t.executeSql("""
                CREATE TEMPORARY VIEW flow_dedup AS
                SELECT * FROM (
                    SELECT *,ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY ingest_time_ms DESC) AS row_num
                    FROM flow_source
                ) WHERE row_num=1
                """);
        t.executeSql(tdSink(
                "td_fund_flows",
                """
                    ts TIMESTAMP(3), event_id STRING, ingest_ts TIMESTAMP(3), trading_date STRING,
                    entity_name STRING, latest_price DOUBLE, change_pct DOUBLE,
                    main_net_inflow DOUBLE, main_net_ratio DOUBLE, super_large_net DOUBLE,
                    large_net DOUBLE, medium_net DOUBLE, small_net DOUBLE, source STRING,
                    is_degraded BOOLEAN, entity_type STRING, entity_code STRING, tbname STRING
                """,
                jdbcUrl, database, "fund_flows"
        ));
    }

    private static void createNewsTables(
            StreamTableEnvironment t, String brokers, String topic, String jdbcUrl, String database
    ) {
        t.executeSql("""
                CREATE TABLE news_source (
                    event_id STRING, event_time_ms BIGINT, ingest_time_ms BIGINT,
                    source STRING, sentiment STRING, sentiment_score DOUBLE, news_tbname STRING,
                    event_ts AS TO_TIMESTAMP_LTZ(event_time_ms,3),
                    WATERMARK FOR event_ts AS event_ts - INTERVAL '5' SECOND
                ) WITH (
                    'connector'='kafka','topic'='%s','properties.bootstrap.servers'='%s',
                    'properties.group.id'='ashare-flink-news-v1','scan.startup.mode'='group-offsets',
                    'format'='json','json.ignore-parse-errors'='false'
                )
                """.formatted(sql(topic), sql(brokers)));
        t.executeSql("""
                CREATE TEMPORARY VIEW news_dedup AS
                SELECT * FROM (
                    SELECT *,ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY ingest_time_ms DESC) AS row_num
                    FROM news_source
                ) WHERE row_num=1
                """);
        t.executeSql(tdSink(
                "td_news_sentiment",
                """
                    ts TIMESTAMP(3), window_end TIMESTAMP(3), event_count BIGINT,
                    positive_count BIGINT, negative_count BIGINT, neutral_count BIGINT,
                    average_score DOUBLE, source STRING, tbname STRING
                """,
                jdbcUrl, database, "news_sentiment_1m"
        ));
    }

    private static String tdSink(
            String name, String columns, String jdbcUrl, String database, String stable
    ) {
        return """
                CREATE TABLE %s (%s) WITH (
                    'connector'='tdengine-connector',
                    'td.jdbc.mode'='sink',
                    'td.jdbc.url'='%s',
                    'sink.db.name'='%s',
                    'sink.supertable.name'='%s',
                    'sink.batch.size'='1000'
                )
                """.formatted(name, columns, sql(jdbcUrl), sql(database), sql(stable));
    }

    private static String env(String key, String fallback) {
        String value = System.getenv(key);
        return value == null || value.isBlank() ? fallback : value.trim();
    }

    private static String identifier(String value) {
        if (!value.matches("[A-Za-z][A-Za-z0-9_]{0,63}")) {
            throw new IllegalArgumentException("Unsafe TDengine database identifier");
        }
        return value;
    }

    private static String sql(String value) {
        return value.replace("'", "''");
    }
}
