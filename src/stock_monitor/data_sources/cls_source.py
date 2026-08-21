"""Authorized CLS (财联社) news REST adapter.

CLS public pages are not scraped. Users must supply a written-authorized API
base URL, endpoint and bearer token matching their contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import requests

from stock_monitor.models import NewsItem

from .common import ProviderError
from .throttle import RequestThrottle


class CLSAuthorizedNewsProvider:
    name = "财联社授权资讯接口"

    def __init__(
        self,
        base_url: str,
        endpoint: str,
        token: str,
        timeout: float = 30.0,
        request_min_interval: float = 0.2,
        throttle: RequestThrottle | None = None,
    ) -> None:
        if not base_url.strip() or not endpoint.strip() or not token.strip():
            raise ProviderError("财联社资讯接入需要授权 API 地址、端点和 Token")
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint.lstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        self._throttle = throttle or RequestThrottle(request_min_interval)

    def _request(self, limit: int, cursor: str | None = None) -> dict[str, Any] | list[Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["after_id"] = cursor
        try:
            self._throttle.wait()
            response = requests.get(
                f"{self.base_url}/{self.endpoint}",
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            raise ProviderError(f"财联社授权接口请求失败：{error}") from error

    def test_connection(self) -> str:
        self._request(1)
        return "财联社授权资讯接口连接正常"

    def fetch_news(self, limit: int = 100, cursor: str | None = None) -> Iterable[NewsItem]:
        payload = self._request(limit, cursor)
        items = payload if isinstance(payload, list) else payload.get("data", payload.get("items", []))
        if isinstance(items, dict):
            items = items.get("items", items.get("list", []))
        if not isinstance(items, list):
            raise ProviderError("财联社授权接口 data/items 字段不是列表；请按合同响应调整字段映射")
        for raw in items:
            if not isinstance(raw, dict):
                continue
            external_id = raw.get("id", raw.get("article_id", raw.get("telegraph_id")))
            published = raw.get("published_at", raw.get("publish_time", raw.get("ctime")))
            title = raw.get("title", raw.get("content", raw.get("brief")))
            if external_id is None or published is None or not title:
                continue
            if isinstance(published, (int, float)):
                epoch = float(published)
                published_at = datetime.fromtimestamp(epoch / 1000 if epoch > 100_000_000_000 else epoch, tz=timezone.utc)
            else:
                published_at = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            if published_at.tzinfo is None or published_at.utcoffset() is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            symbols = raw.get("symbols", raw.get("stocks", []))
            if isinstance(symbols, str):
                symbols = [part.strip() for part in symbols.split(",")]
            yield NewsItem(
                external_id=str(external_id),
                source="财联社",
                published_at=published_at,
                title=str(title),
                summary=str(raw.get("summary", raw.get("content", ""))) or None,
                url=str(raw.get("url")) if raw.get("url") else None,
                symbols=symbols if isinstance(symbols, list) else [],
            )
