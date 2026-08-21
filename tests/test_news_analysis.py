from datetime import datetime, timezone

from stock_monitor.models import NewsItem
from stock_monitor.news_analysis import enrich_news, infer_symbols


def _news(title: str, summary: str | None = None) -> NewsItem:
    return NewsItem(
        external_id="1",
        source="财联社",
        published_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        title=title,
        summary=summary,
    )


def test_positive_news_requires_exact_evidence():
    item = enrich_news(_news("公司业绩预增并拟回购股份"))
    assert item.sentiment == "利好"
    assert item.evidence == "业绩预增"
    assert item.evidence in item.title
    assert item.confidence >= 0.8


def test_conflicting_news_is_neutral_with_both_evidence_terms():
    item = enrich_news(_news("公司净利润增长，但收到行政处罚"))
    assert item.sentiment == "中性"
    assert "净利润增长" in item.evidence
    assert "行政处罚" in item.evidence


def test_symbol_matching_combines_code_and_watchlist_name():
    symbols = infer_symbols("贵州茅台 600519 发布公告，宁德时代跟进", [
        {"symbol": "300750.SZ", "name": "宁德时代"},
    ])
    assert symbols == ["300750.SZ", "600519.SH"]


def test_directionless_news_stays_neutral_without_fabricated_evidence():
    item = enrich_news(_news("公司召开年度股东大会"))
    assert item.sentiment == "中性"
    assert item.evidence is None
