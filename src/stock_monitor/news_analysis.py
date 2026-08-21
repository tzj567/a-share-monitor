"""Deterministic, evidence-first Chinese financial-news classification."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import NewsItem


POSITIVE_RULES: tuple[tuple[str, float], ...] = (
    ("扭亏为盈", 0.95), ("业绩预增", 0.9), ("净利润增长", 0.9),
    ("净利润同比增长", 0.9), ("中标", 0.78), ("增持", 0.76),
    ("回购", 0.72), ("订单增长", 0.72), ("上调评级", 0.75),
    ("获得批准", 0.7), ("签订重大合同", 0.74),
)
NEGATIVE_RULES: tuple[tuple[str, float], ...] = (
    ("立案调查", 0.96), ("暂停上市", 0.97), ("业绩预亏", 0.92),
    ("亏损扩大", 0.9), ("净利润下降", 0.86), ("下调评级", 0.78),
    ("减持", 0.74), ("违约", 0.9), ("行政处罚", 0.88),
    ("警示函", 0.8), ("终止重组", 0.76),
)
CODE_PATTERN = re.compile(r"(?<!\d)((?:00|30|60|68)\d{4}|[489]\d{5})(?:\.(SH|SZ|BJ))?(?!\d)", re.IGNORECASE)


def _normalize_code(code: str, suffix: str | None = None) -> str:
    if suffix:
        return f"{code}.{suffix.upper()}"
    if code.startswith(("6", "5", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def infer_symbols(text: str, watchlist: Iterable[dict]) -> list[str]:
    symbols = {_normalize_code(code, suffix) for code, suffix in CODE_PATTERN.findall(text)}
    for item in watchlist:
        symbol = str(item.get("symbol", "")).upper()
        name = str(item.get("name") or "").strip()
        if symbol and (symbol in text.upper() or symbol.split(".", 1)[0] in text):
            symbols.add(symbol)
        if name and len(name) >= 2 and name in text:
            symbols.add(symbol)
    return sorted(symbols)


def _best_rule(text: str, rules: tuple[tuple[str, float], ...]) -> tuple[str, float] | None:
    matches = [(keyword, confidence) for keyword, confidence in rules if keyword in text]
    return max(matches, key=lambda item: item[1]) if matches else None


def enrich_news(item: NewsItem, watchlist: Iterable[dict] = ()) -> NewsItem:
    """Attach symbols and a sentiment only when the source text contains evidence."""
    text = " ".join(part for part in (item.title, item.summary or "") if part)
    positive = _best_rule(text, POSITIVE_RULES)
    negative = _best_rule(text, NEGATIVE_RULES)
    if positive and negative:
        sentiment, score, evidence, confidence = "中性", 0.0, f"同时出现“{positive[0]}”与“{negative[0]}”", 0.5
    elif positive:
        sentiment, score, evidence, confidence = "利好", positive[1], positive[0], positive[1]
    elif negative:
        sentiment, score, evidence, confidence = "利空", -negative[1], negative[0], negative[1]
    else:
        sentiment, score, evidence, confidence = "中性", 0.0, None, 0.35

    # This exact-substring invariant is the hard anti-hallucination guard.
    if evidence and not all(part.strip("“”") in text for part in re.findall(r"“([^”]+)”", evidence) or [evidence]):
        sentiment, score, evidence, confidence = "中性", 0.0, None, 0.0
    symbols = sorted(set(item.symbols) | set(infer_symbols(text, watchlist)))
    return item.model_copy(update={
        "symbols": symbols,
        "sentiment": sentiment,
        "sentiment_score": score,
        "evidence": evidence,
        "confidence": confidence,
    })
