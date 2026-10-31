"""回退决策的回归测试 —— 这是最贵的一段逻辑。

生图/生视频是计费操作。判错的代价只有两种，都很痛：
- 该换家却判成「不换」→ 用户卡在一家没额度的供应商上，反复失败；
- 不该换却判成「换」→ 上游已经出图并扣费，又发给第二家，双倍扣费。

所以这里的用例不是「跑通」，而是把每一条边界钉死。
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("astrbot", reason="service 依赖 astrbot.api.logger，需在容器内跑")

from core.http import HTTP, BackendError  # noqa: E402
from core.queue import QueueFullError  # noqa: E402
from core.service import _should_fallback  # noqa: E402


# ---------------------------------------------------------------- 值得换家


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_auth_quota_and_not_found_switch_provider(status):
    assert _should_fallback(BackendError("上游拒绝", status=status)) is True


@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_errors_switch_provider(status):
    assert _should_fallback(BackendError("上游炸了", status=status)) is True


def test_queue_full_switches_provider():
    assert _should_fallback(QueueFullError("队列满了")) is True


@pytest.mark.parametrize(
    "message",
    [
        "ClientConnectorError: Cannot connect to host",
        "Connection refused",
        "DNS resolution failed",
        "network unreachable",
    ],
)
def test_connection_failures_switch_provider(message):
    """请求根本没到上游，换家不会重复计费。"""
    assert _should_fallback(BackendError(message, status=0)) is True


# ---------------------------------------------------------------- 不值得换家


def test_504_never_switches_provider():
    """上游网关超时：任务可能已经跑完并计费，换家就是双扣。"""
    assert _should_fallback(BackendError("Gateway Timeout", status=504)) is False


def test_504_does_not_fallback_even_with_timeout_wording():
    """状态码优先于文案，别被 message 里的 timeout 干扰。"""
    assert _should_fallback(BackendError("请求超时 timeout", status=504)) is False


@pytest.mark.parametrize("status", [400, 409, 422])
def test_client_errors_do_not_switch_provider(status):
    """参数错换家也一样错，纯浪费一次调用。"""
    assert _should_fallback(BackendError("参数不对", status=status)) is False


@pytest.mark.parametrize("message", ["ReadTimeout", "请求超时", "timed out after 600s"])
def test_timeout_after_request_sent_does_not_switch(message):
    """请求已发出后超时，可能已经计费。"""
    assert _should_fallback(BackendError(message, status=0)) is False


def test_asyncio_timeout_does_not_switch():
    assert _should_fallback(asyncio.TimeoutError()) is False


def test_unknown_error_does_not_switch():
    """认不出来的错误保守处理：宁可不换，也不冒双扣风险。"""
    assert _should_fallback(BackendError("something went wrong", status=0)) is False


# ---------------------------------------------------------------- HTTP 层重试
# 注意：HTTP 层重试比回退更早发生，边界必须和 _should_fallback 一致。


@pytest.mark.parametrize(
    "method,status,expected",
    [
        ("POST", 504, False),  # 核心：生图 POST 遇到 504 绝不重发
        ("GET", 504, True),  # 轮询/探测可以重试
        ("POST", 500, True),
        ("POST", 502, True),
        ("POST", 503, True),
        ("POST", 408, True),
        ("POST", 429, False),  # 交给供应商队列处理
        ("POST", 400, False),
        ("POST", 401, False),
        ("POST", 200, False),
    ],
)
def test_retryable_status_matrix(method, status, expected):
    assert HTTP._retryable_status(method, status) is expected


def test_retryable_status_is_case_insensitive():
    assert HTTP._retryable_status("get", 504) is True
    assert HTTP._retryable_status("post", 504) is False
