"""妙笔 · HTTP 封装与统一错误。

429 不在这里重试：节流由供应商队列负责，重试只会让冷却失效、拖长等待。
"""

from __future__ import annotations

import asyncio
import json as jsonlib
from typing import Any

import aiohttp


class BackendError(RuntimeError):
    """带上游状态码的异常，队列据此判断是否计冷却。"""

    def __init__(self, message: str, *, status: int = 0, payload: Any = None):
        super().__init__(message)
        self.status = int(status or 0)
        self.payload = payload


class Response:
    __slots__ = ("status", "data", "headers", "url")

    def __init__(self, status: int, data: bytes, headers: dict[str, str], url: str):
        self.status = status
        self.data = data
        self.headers = headers
        self.url = url

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace")

    def json(self) -> Any:
        try:
            return jsonlib.loads(self.data.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            raise BackendError(
                f"响应不是合法 JSON（{self.url}）: {self.text[:200]}",
                status=self.status,
            ) from exc

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Response {self.status} {self.url} {len(self.data)}B>"


class HTTP:
    def __init__(
        self,
        *,
        timeout: int = 600,
        proxy: str | None = None,
        retries: int = 1,
        headers: dict[str, str] | None = None,
    ):
        self.timeout = max(10, int(timeout or 600))
        self.proxy = (proxy or "").strip() or None
        self.retries = max(0, int(retries or 0))
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        # 复用连接：轮询场景一次任务要发十几次请求，每次新建 session 都要重新握手
        self._session: aiohttp.ClientSession | None = None
        self._session_loop: object | None = None

    def _session_for(self) -> aiohttp.ClientSession:
        loop = asyncio.get_running_loop()
        if (
            self._session is None
            or self._session.closed
            or self._session_loop is not loop
        ):
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=8, ttl_dns_cache=120)
            )
            self._session_loop = loop
        return self._session

    async def aclose(self) -> None:
        """任务结束时释放连接池；不调用会留下 Unclosed client session 警告。"""
        session, self._session = self._session, None
        self._session_loop = None
        if session is not None and not session.closed:
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _retryable_status(method: str, status: int) -> bool:
        """哪些状态码可以安全重发。

        504 单独处理：上游网关超时说明请求可能已经到达业务端并开始计费，
        POST 生图/生视频再发一次就是双扣。只有 GET（轮询、探测模型）才重试。
        """
        if status == 504:
            return str(method).upper() == "GET"
        return status in (408, 500, 502, 503)

    async def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        expect: tuple[int, ...] = (200, 201, 202),
    ) -> Response:
        merged = dict(self.headers)
        merged.update(headers or {})
        body = None
        if json_body is not None:
            body = jsonlib.dumps(json_body, ensure_ascii=False).encode("utf-8")
            merged.setdefault("Content-Type", "application/json")
        elif data is not None:
            body = data

        attempts = self.retries + 1
        last_error: Exception | None = None
        upper = method.upper()
        for attempt in range(attempts):
            client_timeout = aiohttp.ClientTimeout(total=float(self.timeout))
            try:
                session = self._session_for()
                async with session.request(
                    upper,
                    url,
                    data=body,
                    headers=merged,
                    params=params,
                    proxy=self.proxy,
                    timeout=client_timeout,
                ) as response:
                    raw = await response.read()
                    if response.status in expect:
                        return Response(
                            response.status,
                            raw,
                            {k.lower(): v for k, v in response.headers.items()},
                            url,
                        )
                    text = raw[:400].decode("utf-8", "replace")
                    error = BackendError(
                        f"HTTP {response.status} {upper} {url} -> {text or '(空响应)'}",
                        status=response.status,
                        payload=_maybe_json(raw),
                    )
                    # 429 交给供应商队列处理；504 只对 GET 重试（见 _retryable_status）
                    if (
                        self._retryable_status(upper, response.status)
                        and attempt + 1 < attempts
                    ):
                        last_error = error
                        await asyncio.sleep(min(2 ** attempt, 6))
                        continue
                    raise error
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = BackendError(
                    f"{upper} {url} 请求失败: {type(exc).__name__}: {exc}", status=0
                )
                # 只有「连接都没建立起来」才安全重发；请求已发出后超时可能已计费
                safe_to_retry = (
                    isinstance(exc, aiohttp.ClientConnectorError) or upper == "GET"
                )
                if attempt + 1 >= attempts or not safe_to_retry:
                    raise last_error from exc
                await asyncio.sleep(min(2 ** attempt, 6))
        if last_error:
            raise last_error
        raise BackendError(f"{upper} {url} 未知错误")

    async def get(self, url: str, **kwargs: Any) -> Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> Response:
        return await self.request("POST", url, **kwargs)


def _maybe_json(raw: bytes) -> Any:
    try:
        return jsonlib.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
