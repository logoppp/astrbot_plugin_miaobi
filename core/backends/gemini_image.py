"""Google Gemini 原生图片协议（nano banana / gemini-3-pro-image 等）。

原生端点不是 OpenAI 兼容格式：
    POST {base}/v1beta/models/{model}:generateContent
    header: x-goog-api-key
    图片在 candidates[].content.parts[].inlineData.data

Gemini 只认固定的比例枚举 + 档位尺寸（512/1K/2K/4K），
不吃精确宽高，所以这里把 sizing 的结果贴到最近的比例上。
"""

from __future__ import annotations

import base64
import re
from typing import Any

from ..http import BackendError
from ._util import decode_b64, guess_mime, pick, ratio_from_size, snap_ratio
from .base import CAP_I2I, CAP_T2I, Backend

_GEMINI_RATIOS = [
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
    "1:4",
    "4:1",
    "1:8",
    "8:1",
]

_MODEL_TAIL_RE = re.compile(r"/models/.*$", re.IGNORECASE)
_VERSION_RE = re.compile(r"/v\d+(?:beta\d*)?$", re.IGNORECASE)


def normalize_gemini_base(url: str) -> str:
    base = str(url or "").strip().rstrip("/")
    if not base:
        return "https://generativelanguage.googleapis.com"
    if not re.match(r"^https?://", base, re.IGNORECASE):
        base = "https://" + base
    base = _MODEL_TAIL_RE.sub("", base)
    if not _VERSION_RE.search(base):
        base += "/v1beta"
    return base


class GeminiImageBackend(Backend):
    protocol = "gemini_image"
    label = "Google Gemini 原生图片"
    capabilities = (CAP_T2I, CAP_I2I)
    # Gemini 出图边长由比例 + 档位决定，这里只兜底防止离谱值
    protocol_limits = {"min_side": 512, "max_side": 4096, "align": 8}
    param_schema = {
        "image_size": {
            "type": "enum",
            "label": "输出档位",
            "options": ["1K", "2K", "4K"],
            "default": "1K",
        },
        "aspect_ratio": {
            "type": "enum",
            "label": "画面比例",
            "options": _GEMINI_RATIOS,
            "advanced": True,
        },
        "temperature": {"type": "float", "label": "温度", "advanced": True},
        "safety_off": {
            "type": "bool",
            "label": "放宽安全过滤",
            "default": False,
            "advanced": True,
        },
    }

    models_supported = True
    models_note = "只列出该 Key 可见的模型；出图请选名字里带 image 的（如 gemini-3-pro-image）。"

    @property
    def base(self) -> str:
        return normalize_gemini_base(self.provider.get("base_url") or "")

    def models_url(self) -> str:
        return f"{self.base}/models"

    def models_headers(self) -> dict[str, str]:
        key = self.api_key()
        if not key:
            raise BackendError(
                f"供应商「{self.provider_name}」未配置 API Key", status=401
            )
        return {"x-goog-api-key": key}

    @property
    def endpoint(self) -> str:
        return f"{self.base}/models/{self.model_id}:generateContent"

    # ---------- 构造 ----------

    def _parts(self, prompt: str, images: list[bytes] | None) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for data in images or []:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": guess_mime(data),
                        "data": base64.b64encode(data).decode("ascii"),
                    }
                }
            )
        parts.append({"text": prompt})
        return parts

    def _aspect(self, size: str, aspect_ratio: str, params: dict[str, Any]) -> str:
        explicit = str(params.get("aspect_ratio") or aspect_ratio or "").strip()
        if explicit:
            return snap_ratio(explicit, _GEMINI_RATIOS, "1:1")
        return ratio_from_size(size, allowed=_GEMINI_RATIOS) or "1:1"

    def _body(
        self, prompt: str, size: str, aspect_ratio: str, images: list[bytes] | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        generation: dict[str, Any] = {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": self._aspect(size, aspect_ratio, params)},
        }
        image_size = str(params.get("image_size") or "").strip().upper()
        if image_size in {"1K", "2K", "4K", "512"}:
            generation["imageConfig"]["imageSize"] = image_size
        if params.get("temperature") is not None:
            generation["temperature"] = float(params["temperature"])

        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": self._parts(prompt, images)}],
            "generationConfig": generation,
        }
        if params.get("safety_off"):
            body["safetySettings"] = [
                {"category": category, "threshold": "BLOCK_NONE"}
                for category in (
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                )
            ]
        return body

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
        key = self.api_key()
        if not key:
            raise BackendError(f"供应商「{self.provider_name}」未配置 API Key", status=401)
        headers = {"x-goog-api-key": key}
        headers.update(self.provider.get("headers") or {})

        response = await self.http.post(
            self.endpoint, json_body=self._body(prompt, size, aspect_ratio, images, merged), headers=headers
        )
        return self._parse(response.json())

    # ---------- 响应 ----------

    def _parse(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise BackendError("Gemini 返回格式异常", payload=payload)
        feedback = payload.get("promptFeedback") or {}
        block = pick(feedback, "blockReason")
        if block:
            raise BackendError(f"提示词被拦截: {block}", payload=payload)

        items: list[dict[str, Any]] = []
        text_bits: list[str] = []
        for candidate in payload.get("candidates") or []:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                inline = pick(part, "inlineData", "inline_data")
                if isinstance(inline, dict):
                    raw = pick(inline, "data")
                    if raw:
                        items.append(
                            self.image_item(
                                data=decode_b64(str(raw)),
                                mime=str(pick(inline, "mimeType", "mime_type", default="image/png")),
                            )
                        )
                    continue
                text = pick(part, "text")
                if text:
                    text_bits.append(str(text))
        if not items:
            hint = ("；".join(text_bits))[:200] if text_bits else str(payload)[:200]
            raise BackendError(f"Gemini 未返回图片: {hint}", payload=payload)
        return self.result(items, text="\n".join(text_bits)[:800], usage=payload.get("usageMetadata"))
