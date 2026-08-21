from unittest.mock import Mock, patch

from stock_monitor.data_sources.cls_source import CLSAuthorizedNewsProvider
from stock_monitor.data_sources.ifind_source import IFindProvider
from stock_monitor.data_sources.throttle import RequestThrottle


def test_request_throttle_waits_between_calls_with_injected_clock():
    now = [0.0]
    delays: list[float] = []

    def clock() -> float:
        return now[0]

    def sleeper(delay: float) -> None:
        delays.append(delay)
        now[0] += delay

    throttle = RequestThrottle(0.5, clock=clock, sleeper=sleeper)
    throttle.wait()
    now[0] = 0.1
    throttle.wait()
    throttle.wait()

    assert delays == [0.4, 0.5]


def test_request_throttle_rejects_negative_interval():
    try:
        RequestThrottle(-0.1)
    except ValueError as error:
        assert "non-negative" in str(error)
    else:
        raise AssertionError("negative intervals must be rejected")


def test_ifind_post_uses_injected_throttle():
    throttle = Mock()
    provider = IFindProvider("token", throttle=throttle)
    provider._cached_access_token = "access"
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"errorcode": 0, "data": {}}
    with patch("stock_monitor.data_sources.ifind_source.requests.post", return_value=response):
        provider._post("endpoint", {})
    throttle.wait.assert_called_once_with()


def test_cls_request_uses_injected_throttle():
    throttle = Mock()
    provider = CLSAuthorizedNewsProvider("https://licensed.example", "news", "token", throttle=throttle)
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"items": []}
    with patch("stock_monitor.data_sources.cls_source.requests.get", return_value=response):
        list(provider.fetch_news())
    throttle.wait.assert_called_once_with()
