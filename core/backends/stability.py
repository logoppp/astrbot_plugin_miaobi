"""Stability AI 图片协议（v2beta）。

端点按模型名分派：
    core / ultra → /v2beta/stable-image/generate/{core|ultra}
    其余（sd3 / sd3.5-*）→ /v2beta/stable-image/generate/sd3，模型名走 `model` 字段

请求是 multipart/form-data，响应按 `Accept: application/json` 返回 base64。
图生图把图片作为 `image` 文件字段，用 `strength` 控制改动幅度。
"""

from __future__ import annotations

import re
from typing import Any

from aiohttp import FormData

from ..http import BackendError
from ._util import pick, ratio_from_size, snap_ratio
from .base import CAP_I2I, CAP_T2I, Backend

_VERSION_RE = re.compile(r"/v\d+[a-z]*(?:beta)?/?$", re.IGNORECASE)
_ENDPOINT_RE = re.compile(r"/stable-image/generate/.*$", re.IGNORECASE)

_RATIOS = ["1:1", "16:9", "9:16", "3:2", "2:3", "4:5", "5:4", "21:9", "9:21", "1:1"]


def normalize_stability_base(url: str) -> str:
    base = str(url or "").strip().rstrip("/")
    if not base:
        base = "https://api.stability.ai"
    if not re.match(r"^https?://", base, re.IGNORECASE):
        base = "https://" + base
    base = _ENDPOINT_RE.sub("", base).rstrip("/")
    if not _VERSION_RE.search(base):
        base += "/v2beta"
    return base


class StabilityBackend(Backend):
    protocol = "stability"
    label = "Stability AI"
    capabilities = (CAP_T2I, CAP_I2I)
    protocol_limits = {"min_side": 256, "max_side": 2048, "align": 8}
    param_schema = {
        "negative_prompt": {"type": "string", "label": "反向提示词", "advanced": True},
        "aspect_ratio": {
            "type": "enum",
            "label": "画面比例",
            "options": _RATIOS,
            "advanced": True,
        },
        "output_format": {
            "type": "enum",
            "label": "输出格式",
            "options": ["png", "jpeg", "webp"],
            "default": "png",
        },
        "strength": {"type": "float", "label": "参考图改动幅度", "default": 0.65, "advanced": True},
        "style_preset": {"type": "string", "label": "风格预设", "advanced": True},
        "seed": {"type": "int", "label": "随机种子", "advanced": True},
    }

    @property
    def base(self) -> str:
        return normalize_stability_base(self.provider.get("base_url") or "")

    @property
    def engine(self) -> str:
        head = str(self.model_id or "").strip().lower()
        if head in {"core", "ultra", "sd3"}:
            return head
        return "sd3" if head.startswith("sd3") else "core"

    @property
    def endpoint(self) -> str:
        return f"{self.base}/stable-image/generate/{self.engine}"

    def _aspect(self, size: str, aspect_ratio: str, params: dict[str, Any]) -> str:
        explicit = str(params.get("aspect_ratio") or aspect_ratio or "").strip()
        if explicit:
            return snap_ratio(explicit, _RATIOS, "1:1")
        return ratio_from_size(size, allowed=_RATIOS) or "1:1"

    async def generate_image(
        self,
        prompt: str,
        *,
        size: str = "",
        aspect_ratio: str = "",
        images: list[bytes] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged = self.merge_params(params)
        ratio = self._aspect(size, aspect_ratio, merged)
        merged.pop("aspect_ratio", None)

        form = FormData()
        form.add_field("prompt", prompt)
        form.add_field("aspect_ratio", ratio)
        form.add_field("output_format", str(merged.pop("output_format", "png") or "png"))
        if self.engine == "sd3" and self.model_id.lower() not in {"sd3"}:
            form.add_field("model", self.model_id)

        if images:
            form.add_field(
                "image",
                images[0],
                filename="reference.png",
                content_type="image/png",
            )
            form.add_field("strength", str(merged.pop("strength", 0.65) or 0.65))

        for key, value in merged.items():
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list)):
                continue
            form.add_field(key, str(value))

        headers = self.auth_headers()
        headers["Accept"] = "application/json"
        response = await self.http.post(self.endpoint, data=form, headers=headers)
        return self._parse(response.json())

    def _parse(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise BackendError("Stability 返回格式异常", payload=payload)
        errors = payload.get("errors")
        if errors:
            text = "；".join(str(item) for item in errors if item)
            raise BackendError(f"Stability 报错: {text}", payload=payload)
        b64 = pick(payload, "image", "artifacts")
        if isinstance(b64, list):
            items = [
                self.image_item(data=_decode(str(entry.get("base64"))), mime="image/png")
                for entry in b64
                if isinstance(entry, dict) and entry.get("base64")
            ]
            if items:
                return self.result(items, raw=payload)
            b64 = None
        if not b64:
            raise BackendError(
                f"Stability 未返回图片: {str(payload)[:200]}", payload=payload
            )
        item = self.image_item(
            data=_decode(str(b64)),
            mime="image/png",
            seed=pick(payload, "seed"),
        )
        return self.result([item], finish_reason=pick(payload, "finish_reason"))


def _decode(text: str) -> bytes:
    from ._util import decode_b64

    return decode_b64(text)
