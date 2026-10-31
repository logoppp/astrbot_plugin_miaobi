"""自定义协议。

不写代码也能接任意线上接口：请求体全部由用户在 `extra_body` 里定义，
用占位符注入运行时数据；响应靠 `params._result_path` 按路径取值。

请求体模板占位符（字符串内联替换）：
    {prompt} {model} {size} {width} {height} {aspect_ratio} {duration} {seed}
    {image}       —— 单张参考图的 data URL
    {image_list}  —— 参考图 URL 数组（占位符独占一个值时替换成真正的数组）

保留参数（写在 model.params 里，不会进请求体）：
    _method         HTTP 方法，默认 POST
    _result_path    响应提取路径，如 data.0.url / output.images.*.url
    _kind           image 或 video，默认 image
    _status_path    异步任务状态字段路径（可选）
    _done_values    视为成功的状态值，逗号分隔
    _poll_url       轮询地址模板，可用 {task_id}
    _task_path      从首个响应里取 task_id 的路径
"""

from __future__ import annotations

import re
from typing import Any

from ..http import BackendError
from ._util import data_url, decode_b64, guess_mime, pick, poll_until
from .base import CAP_I2I, CAP_T2I, CAP_T2V, Backend

_RESERVED = {
    "_method",
    "_result_path",
    "_kind",
    "_status_path",
    "_done_values",
    "_poll_url",
    "_task_path",
    "_headers",
}

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def _render(value: Any, context: dict[str, Any]) -> Any:
    """递归替换模板里的占位符；整串就是占位符时保留原始类型。"""
    if isinstance(value, dict):
        return {key: _render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, context) for item in value]
    if not isinstance(value, str):
        return value
    whole = _PLACEHOLDER_RE.fullmatch(value.strip())
    if whole:
        key = whole.group(1)
        if key in context:
            return context[key]
        return ""
    return _PLACEHOLDER_RE.sub(
        lambda match: str(context.get(match.group(1), "")), value
    )


def _dig(data: Any, path: str) -> list[Any]:
    """按 `a.b.0.c` 或 `a.*.c` 取值，返回命中的全部结果。"""
    if not path:
        return []
    nodes = [data]
    for token in str(path).split("."):
        token = token.strip()
        if not token:
            continue
        next_nodes: list[Any] = []
        for node in nodes:
            if token == "*":
                if isinstance(node, list):
                    next_nodes.extend(node)
                elif isinstance(node, dict):
                    next_nodes.extend(node.values())
                continue
            if isinstance(node, dict) and token in node:
                next_nodes.append(node[token])
                continue
            if isinstance(node, list):
                try:
                    next_nodes.append(node[int(token)])
                except (ValueError, IndexError):
                    continue
        nodes = next_nodes
        if not nodes:
            return []
    return nodes


class CustomBackend(Backend):
    protocol = "custom"
    label = "自定义接口"
    capabilities = (CAP_T2I, CAP_I2I, CAP_T2V)
    protocol_limits = {}
    # 字段由用户模板里的 {占位符} 决定，无法枚举，因此不做白名单
    accept_all_params = True
    param_schema = {
        "_method": {
            "type": "enum",
            "label": "HTTP 方法",
            "options": ["POST", "GET"],
            "default": "POST",
            "advanced": True,
        },
        "_result_path": {
            "type": "string",
            "label": "结果路径",
            "default": "data.*.url",
            "advanced": True,
        },
        "_kind": {
            "type": "enum",
            "label": "结果类型",
            "options": ["image", "video"],
            "default": "image",
            "advanced": True,
        },
        "_status_path": {"type": "string", "label": "状态字段路径", "advanced": True},
        "_done_values": {"type": "string", "label": "完成状态值", "advanced": True},
        "_poll_url": {"type": "string", "label": "轮询地址模板", "advanced": True},
        "_task_path": {"type": "string", "label": "任务 ID 路径", "advanced": True},
        "_headers": {"type": "string", "label": "额外请求头（JSON）", "advanced": True},
    }

    @property
    def endpoint(self) -> str:
        base = str(self.provider.get("base_url") or "").strip()
        if not base:
            raise BackendError(f"供应商「{self.provider_name}」未配置 base_url")
        return base

    def _split_params(self, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        reserved = {k: v for k, v in params.items() if k in _RESERVED}
        plain = {k: v for k, v in params.items() if k not in _RESERVED}
        return reserved, plain

    def _context(
        self,
        prompt: str,
        size: str,
        aspect_ratio: str,
        duration: int,
        images: list[bytes] | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        width = height = ""
        text = str(size or "").lower().replace("×", "x")
        if "x" in text:
            left, _, right = text.partition("x")
            width, height = left.strip(), right.strip()
        context: dict[str, Any] = {
            "prompt": prompt,
            "model": self.model_id,
            "size": size,
            "width": width,
            "height": height,
            "aspect_ratio": aspect_ratio,
            "duration": duration,
            "seed": params.get("seed", ""),
            "image": "",
            "image_list": [],
        }
        if images:
            context["image"] = data_url(images[0])
            context["image_list"] = [data_url(data) for data in images]
        return context

    async def _call(
        self,
        prompt: str,
        *,
        size: str,
        aspect_ratio: str,
        duration: int,
        images: list[bytes] | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        merged = self.merge_params(params)
        reserved, plain = self._split_params(merged)
        context = self._context(prompt, size, aspect_ratio, duration, images, plain)

        template = dict(self.provider.get("extra_body") or {})
        template.update(plain)
        body = _render(template, context)
        if not body:
            # 没配模板时至少给一个能用的最小请求体，否则上游收到空对象
            body = {"model": self.model_id, "prompt": prompt}
            if size:
                body["size"] = size

        headers = self.auth_headers() if (self.provider.get("api_keys") or []) else {}
        extra_headers = reserved.get("_headers")
        if isinstance(extra_headers, str) and extra_headers.strip():
            import json as jsonlib

            try:
                headers.update(jsonlib.loads(extra_headers))
            except Exception as exc:  # noqa: BLE001
                raise BackendError(f"_headers 不是合法 JSON: {exc}") from exc

        method = str(reserved.get("_method") or "POST").upper()
        if method == "GET":
            response = await self.http.get(
                self.endpoint, params={k: str(v) for k, v in body.items()}, headers=headers
            )
        else:
            response = await self.http.post(self.endpoint, json_body=body, headers=headers)

        payload = response.json()
        status_path = str(reserved.get("_status_path") or "").strip()
        poll_template = str(reserved.get("_poll_url") or "").strip()
        if status_path and poll_template:
            task_values = _dig(payload, str(reserved.get("_task_path") or ""))
            task_id = str(task_values[0]) if task_values else ""
            poll_url = _PLACEHOLDER_RE.sub(
                lambda match: str({"task_id": task_id}.get(match.group(1), "")), poll_template
            )
            done_values = {
                item.strip().lower()
                for item in str(reserved.get("_done_values") or "succeeded,success,completed").split(",")
                if item.strip()
            }

            async def _fetch() -> dict[str, Any]:
                resp = await self.http.get(poll_url, headers=headers)
                return resp.json()

            def _state(data: dict[str, Any]) -> str:
                values = _dig(data, status_path)
                return str(values[0]).lower() if values else ""

            payload = await poll_until(
                _fetch,
                is_done=lambda data: _state(data) in done_values,
                failure=lambda data: (
                    f"状态 {_state(data)}"
                    if _state(data) in {"failed", "error", "canceled", "cancelled"}
                    else None
                ),
                timeout=float(self.provider.get("poll_timeout") or 900),
                interval=4.0,
                max_interval=12.0,
            )

        result_path = str(reserved.get("_result_path") or "").strip()
        values = _dig(payload, result_path) if result_path else []
        kind = str(reserved.get("_kind") or "image").strip().lower()
        items: list[dict[str, Any]] = []
        for value in values:
            link = value
            if isinstance(value, dict):
                link = pick(value, "url", "video_url", "image_url", "b64_json", "base64")
            if not isinstance(link, str) or not link.strip():
                continue
            link = link.strip()
            if link.startswith("data:") or (
                len(link) > 256 and not link.startswith("http")
            ):
                data = decode_b64(link)
                mime = guess_mime(data)
                item = (
                    self.video_item(data=data, mime=mime, duration=duration)
                    if kind == "video"
                    else self.image_item(data=data, mime=mime)
                )
            else:
                item = (
                    self.video_item(url=link, duration=duration)
                    if kind == "video"
                    else self.image_item(url=link)
                )
            items.append(item)
        if not items:
            raise BackendError(
                f"按路径「{result_path or '(未配置)'}」没取到结果: {str(payload)[:200]}",
                payload=payload,
            )
        return self.result(items, raw=payload)

    async def generate_image(
        self,
        prompt: str,
        *,
        size: str = "",
        aspect_ratio: str = "",
        images: list[bytes] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._call(
            prompt,
            size=size,
            aspect_ratio=aspect_ratio,
            duration=0,
            images=images,
            params=params or {},
        )

    async def generate_video(
        self,
        prompt: str,
        *,
        size: str = "",
        aspect_ratio: str = "",
        duration: int = 0,
        images: list[bytes] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._call(
            prompt,
            size=size,
            aspect_ratio=aspect_ratio,
            duration=duration,
            images=images,
            params=params or {},
        )
