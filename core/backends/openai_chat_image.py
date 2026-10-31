"""OpenAI Chat Completions 出图协议。

给「把图片当对话回复」的模型用：gpt-4o-image、Gemini / Flux 的各种中转站、
OpenRouter 的 image 模型等。它们不走 /images/generations，而是在
/chat/completions 的回复里塞图片。

各家返回形态极不统一，这里按优先级依次尝试：
    1. message.images[].image_url.url
    2. message.content 为数组，元素含 image_url
    3. message.content 为字符串，含 markdown 图片或裸 data URL / 图片链接
"""

from __future__ import annotations

import re
from typing import Any

from ..http import BackendError
from ._util import data_url, decode_b64, guess_mime, pick
from .base import CAP_I2I, CAP_T2I, Backend

_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)\s*\)")
_BARE_IMAGE_RE = re.compile(
    r"https?://[^\s\"'<>)]+?\.(?:png|jpe?g|webp|gif)(?:\?[^\s\"'<>)]*)?", re.IGNORECASE
)
_DATA_URL_RE = re.compile(
    # 只把「连续且足够长」的 base64 行算进 data URL：否则会把换行后的正文也吞进来
    r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]{16,}(?:\r?\n[A-Za-z0-9+/=]{16,})*",
    re.IGNORECASE,
)


class OpenAIChatImageBackend(Backend):
    protocol = "openai_chat_image"
    label = "OpenAI 对话式出图"
    capabilities = (CAP_T2I, CAP_I2I)
    protocol_limits = {"min_side": 256, "max_side": 4096, "align": 8}
    param_schema = {
        "modalities": {
            "type": "string",
            "label": "模态声明（留空则不发）",
            "default": "image,text",
            "advanced": True,
        },
        "max_tokens": {"type": "int", "label": "最大 token", "advanced": True},
        "temperature": {"type": "float", "label": "温度", "advanced": True},
        "size_hint": {
            "type": "string",
            "label": "尺寸提示（拼进提示词）",
            "default": "1024x1024",
            "advanced": True,
        },
    }

    models_supported = True
    models_note = "来自该中转站的 /models 接口，通常包含对话模型，请只挑能出图的。"

    @property
    def base(self) -> str:
        base = str(self.provider.get("base_url") or "").strip().rstrip("/")
        if not base:
            raise BackendError(f"供应商「{self.provider_name}」未配置 base_url")
        base = re.sub(r"/chat/completions/?$", "", base, flags=re.IGNORECASE)
        if not re.search(r"/v\d+[a-z]*$", base, flags=re.IGNORECASE):
            base += "/v1"
        return base

    def models_url(self) -> str:
        return f"{self.base}/models"

    @property
    def endpoint(self) -> str:
        return f"{self.base}/chat/completions"

    # ---------- 构造 ----------

    def _content(self, prompt: str, images: list[bytes] | None, size: str, params: dict[str, Any]):
        text = prompt
        hint = str(params.get("size_hint") or "").strip()
        if hint and size and "size" not in prompt.lower():
            text = f"{prompt}\n\n（目标尺寸 {size}）"
        if not images:
            return text
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for data in images:
            parts.append({"type": "image_url", "image_url": {"url": data_url(data)}})
        return parts

    def _body(
        self,
        prompt: str,
        size: str,
        images: list[bytes] | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": [
                {"role": "user", "content": self._content(prompt, images, size, params)}
            ],
            "stream": False,
        }
        modalities = str(params.get("modalities") or "").strip()
        if modalities:
            body["modalities"] = [
                item.strip() for item in re.split(r"[,|]", modalities) if item.strip()
            ]
        for key, value in params.items():
            if key in {"modalities", "size_hint"} or value is None or value == "":
                continue
            body.setdefault(key, value)
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
        body = self._body(prompt, size, images, merged)
        response = await self.http.post(
            self.endpoint, json_body=body, headers=self.auth_headers()
        )
        return self._parse(response.json())

    # ---------- 解析 ----------

    def _parse(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise BackendError("对话式出图返回格式异常", payload=payload)
        error = payload.get("error")
        if isinstance(error, dict) and error:
            raise BackendError(
                f"上游报错: {pick(error, 'message', default=str(error)[:200])}",
                payload=payload,
            )
        choices = payload.get("choices") or []
        if not choices:
            raise BackendError(
                f"上游没有返回 choices: {str(payload)[:200]}", payload=payload
            )
        message = choices[0].get("message") or {}
        items: list[dict[str, Any]] = []
        text_bits: list[str] = []

        # 形态 1：message.images
        for entry in message.get("images") or []:
            link = entry
            if isinstance(entry, dict):
                link = pick(entry, "image_url", "url", "image")
                if isinstance(link, dict):
                    link = pick(link, "url")
            if isinstance(link, str) and link.strip():
                items.append(self._to_item(link.strip()))

        # 形态 2/3：content 数组或字符串
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and part.get("text"):
                    text_bits.append(str(part["text"]))
                    continue
                link = pick(part, "image_url", "url", "image")
                if isinstance(link, dict):
                    link = pick(link, "url")
                if isinstance(link, str) and link.strip():
                    items.append(self._to_item(link.strip()))
        elif isinstance(content, str):
            text_bits.append(content)
            for match in _DATA_URL_RE.findall(content):
                try:
                    items.append(self._to_item(match.strip()))
                except BackendError:
                    continue
            for match in _MD_IMAGE_RE.findall(content):
                items.append(self._to_item(match.strip()))
            if not items:
                for match in _BARE_IMAGE_RE.findall(content):
                    items.append(self._to_item(match.strip()))

        if not items:
            hint = "\n".join(text_bits)[:300] or str(payload)[:200]
            raise BackendError(f"回复里没有图片: {hint}", payload=payload)
        return self.result(items, text="\n".join(text_bits)[:800], usage=payload.get("usage"))

    def _to_item(self, link: str) -> dict[str, Any]:
        if link.startswith("data:"):
            data = decode_b64(link)
            return self.image_item(data=data, mime=guess_mime(data))
        return self.image_item(url=link)
