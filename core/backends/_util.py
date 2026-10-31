"""妙笔 · 后端共用工具。

只放与具体平台无关的纯函数：比例换算、data URL、异步任务轮询、JWT 签发。
每个后端都能少写几十行重复代码。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json as jsonlib
import math
import time
from typing import Any, Awaitable, Callable

from ..http import BackendError

# 各平台普遍接受的标准比例
STD_RATIOS: list[tuple[str, float]] = [
    ("1:1", 1.0),
    ("4:3", 4 / 3),
    ("3:4", 3 / 4),
    ("3:2", 1.5),
    ("2:3", 2 / 3),
    ("16:9", 16 / 9),
    ("9:16", 9 / 16),
    ("21:9", 21 / 9),
    ("9:21", 9 / 21),
    ("5:4", 1.25),
    ("4:5", 0.8),
    ("2:1", 2.0),
    ("1:2", 0.5),
]

MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}


def ratio_from_size(size: str, *, allowed: list[str] | None = None) -> str:
    """把 "832x480" 换算成最接近的标准比例；给不出就返回空串。"""
    text = str(size or "").strip().lower().replace("×", "x").replace("*", "x")
    if "x" not in text:
        return ""
    left, _, right = text.partition("x")
    try:
        width, height = float(left), float(right)
    except ValueError:
        return ""
    if width <= 0 or height <= 0:
        return ""
    aspect = width / height
    pool = STD_RATIOS
    if allowed:
        wanted = {str(item) for item in allowed}
        narrowed = [(name, value) for name, value in STD_RATIOS if name in wanted]
        if narrowed:
            pool = narrowed
    best, best_delta = "", None
    for name, value in pool:
        delta = abs(math.log(aspect / value))
        if best_delta is None or delta < best_delta:
            best, best_delta = name, delta
    return best


def snap_ratio(raw: str, allowed: list[str], default: str = "1:1") -> str:
    """把任意比例贴到允许列表上（近似匹配，不是精确相等）。"""
    text = str(raw or "").strip().lower().replace("：", ":").replace("/", ":")
    if text in allowed:
        return text
    if ":" not in text:
        derived = ratio_from_size(text, allowed=allowed)
        return derived or default
    left, _, right = text.partition(":")
    try:
        aspect = float(left) / max(float(right), 1e-6)
    except ValueError:
        return default
    best, best_delta = default, None
    for item in allowed:
        a, _, b = item.partition(":")
        try:
            value = float(a) / max(float(b), 1e-6)
        except ValueError:
            continue
        delta = abs(math.log(aspect / value))
        if best_delta is None or delta < best_delta:
            best, best_delta = item, delta
    return best


def guess_mime(data: bytes, fallback: str = "image/png") -> str:
    """按魔数判断图片类型，上游经常不告诉 mime。"""
    if not data:
        return fallback
    if data[:8].startswith(b"\x89PNG"):
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"\x00\x00\x00\x18" or data[4:8] == b"ftyp":
        return "video/mp4"
    return fallback


def data_url(data: bytes, mime: str = "") -> str:
    mime = mime or guess_mime(data)
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def decode_b64(text: str) -> bytes:
    """容忍 data URL 前缀与缺失 padding 的 base64 解码。"""
    raw = str(text or "").strip()
    if raw.startswith("data:"):
        _, _, raw = raw.partition(",")
    raw = raw.replace("\n", "").replace("\r", "")
    padding = (-len(raw)) % 4
    if padding:
        raw += "=" * padding
    try:
        return base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise BackendError(f"base64 解码失败: {exc}") from exc


def pick(source: Any, *keys: str, default: Any = None) -> Any:
    """多键兼容取值：上游同一含义的字段常换名字。"""
    if not isinstance(source, dict):
        return default
    for key in keys:
        if key in source and source[key] not in (None, ""):
            return source[key]
    return default


def as_list(value: Any) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


async def poll_until(
    fetch: Callable[[], Awaitable[dict[str, Any]]],
    *,
    is_done: Callable[[dict[str, Any]], bool],
    failure: Callable[[dict[str, Any]], str | None],
    timeout: float,
    interval: float = 3.0,
    max_interval: float = 12.0,
    on_tick: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """轮询异步任务直到完成/失败/超时。

    上游任务动辄跑几分钟，这里退避到 max_interval 就不再拉长，
    只为避免把接口打爆，不至于让用户等太久。
    """
    deadline = time.monotonic() + max(30.0, float(timeout))
    delay = max(0.5, float(interval))
    last: dict[str, Any] = {}
    while True:
        last = await fetch()
        if on_tick:
            try:
                on_tick(last)
            except Exception:  # noqa: BLE001
                pass
        reason = failure(last)
        if reason:
            raise BackendError(f"上游任务失败: {reason}", payload=last)
        if is_done(last):
            return last
        if time.monotonic() >= deadline:
            raise BackendError(
                f"等待任务超时（{int(timeout)}s），上游仍未完成", status=504, payload=last
            )
        await asyncio.sleep(delay)
        delay = min(max_interval, delay * 1.4)


def jwt_hs256(access_key: str, secret_key: str, *, ttl: int = 1800) -> str:
    """可灵等平台用 AK/SK 自签 JWT，不依赖 PyJWT。"""

    def _seg(obj: dict[str, Any]) -> bytes:
        raw = jsonlib.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    now = int(time.time())
    head = _seg({"alg": "HS256", "typ": "JWT"})
    body = _seg({"iss": access_key, "exp": now + max(60, ttl), "nbf": now - 5})
    signing = head + b"." + body
    digest = hmac.new(secret_key.encode("utf-8"), signing, hashlib.sha256).digest()
    return (signing + b"." + base64.urlsafe_b64encode(digest).rstrip(b"=")).decode("ascii")


def join_url(base: str, path: str) -> str:
    return str(base or "").rstrip("/") + "/" + str(path or "").lstrip("/")
