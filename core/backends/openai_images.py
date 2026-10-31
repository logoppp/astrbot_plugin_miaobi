"""OpenAI Images 兼容协议。

覆盖：OpenAI（gpt-image）、ComfyUI Roundabout、千问 compatible-mode、硅基流动、智谱、
以及绝大多数「OpenAI 兼容」中转站。

图生图有两种上游走法，由 provider.edit_mode 决定：
    edits        —— POST /images/edits（multipart，OpenAI 标准）
    generations  —— POST /images/generations 并带 image 字段（千问等平台）
"""

from __future__ import annotations

import base64
import re
from typing import Any

from aiohttp import FormData

from ..http import BackendError
from .base import CAP_I2I, CAP_T2I, Backend

_VERSION_RE = re.compile(r"/v\d+[a-z]*(?:/|$)")
_ENDPOINT_RE = re.compile(r"/images/(?:generations|edits?)/?$", re.IGNORECASE)


def normalize_base(url: str) -> str:
    """把用户填的 base_url 收敛成「可直接拼 /images/xxx」的前缀。"""
    base = str(url or "").strip().rstrip("/")
    if not base:
        return ""
    base = _ENDPOINT_RE.sub("", base).rstrip("/")
    if _VERSION_RE.search(base):
        return base
    return base + "/v1"


class OpenAIImagesBackend(Backend):
    protocol = "openai_images"
    label = "OpenAI 兼容图片接口"
    capabilities = (CAP_T2I, CAP_I2I)
    supports_edit_mode = True
    protocol_limits = {"min_side": 64, "max_side": 8192, "align": 8}
    param_schema = {
        "n": {"type": "int", "label": "生成数量", "default": 1, "min": 1, "max": 8},
        "negative_prompt": {"type": "string", "label": "反向提示词", "advanced": True},
        "seed": {"type": "int", "label": "随机种子", "advanced": True},
        "steps": {"type": "int", "label": "步数", "advanced": True},
        "cfg": {"type": "float", "label": "CFG", "advanced": True},
        "quality": {"type": "string", "label": "质量", "advanced": True},
        "response_format": {"type": "string", "label": "返回格式", "advanced": True},
        "prompt_extend": {"type": "bool", "label": "提示词扩写", "advanced": True},
        "watermark": {"type": "bool", "label": "水印", "advanced": True},
    }

    models_supported = True
    models_note = "来自该中转站的 /models 接口，通常把对话模型也一起列出来，请只挑出图 / 出视频的模型。"

    @property
    def base(self) -> str:
        base = normalize_base(self.provider.get("base_url") or "")
        if not base:
            raise BackendError(f"供应商「{self.provider_name}」未配置 base_url")
        return base

    def models_url(self) -> str:
        return f"{self.base}/models"

    @property
    def edit_mode(self) -> str:
        return str(self.provider.get("edit_mode") or "edits").strip().lower()

    # ---------- 请求体 ----------

    def _body(self, prompt: str, size: str, params: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model_id, "prompt": prompt}
        if size:
            body["size"] = size
        for key, value in (params or {}).items():
            if value is None or value == "":
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
        if images:
            if self.edit_mode == "generations":
                payload = await self._edit_via_generations(prompt, size, images, merged)
            else:
                payload = await self._edit_via_edits(prompt, size, images, merged)
        else:
            payload = await self._text_to_image(prompt, size, merged)
        return payload

    async def _text_to_image(
        self, prompt: str, size: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        url = f"{self.base}/images/generations"
        body = self._body(prompt, size, params)
        response = await self.http.post(
            url, json_body=body, headers=self.auth_headers()
        )
        return self._parse(response.json(), url=url)

    async def _edit_via_edits(
        self, prompt: str, size: str, images: list[bytes], params: dict[str, Any]
    ) -> dict[str, Any]:
        url = f"{self.base}/images/edits"
        form = FormData()
        form.add_field("model", self.model_id)
        form.add_field("prompt", prompt)
        if size:
            form.add_field("size", size)
        for key, value in params.items():
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list)):
                continue
            form.add_field(key, str(value))
        multiple = len(images) > 1
        for index, data in enumerate(images):
            field = "image[]" if multiple else "image"
            form.add_field(
                field,
                data,
                filename=f"reference_{index}.png",
                content_type="image/png",
            )
        response = await self.http.post(url, data=form, headers=self.auth_headers())
        return self._parse(response.json(), url=url)

    async def _edit_via_generations(
        self, prompt: str, size: str, images: list[bytes], params: dict[str, Any]
    ) -> dict[str, Any]:
        url = f"{self.base}/images/generations"
        body = self._body(prompt, size, params)
        data_urls = [
            "data:image/png;base64," + base64.b64encode(data).decode("ascii")
            for data in images
        ]
        body["image"] = data_urls[0] if len(data_urls) == 1 else data_urls
        body.setdefault("n", 1)
        response = await self.http.post(
            url, json_body=body, headers=self.auth_headers()
        )
        return self._parse(response.json(), url=url)

    # ---------- 响应 ----------

    def _parse(self, payload: Any, *, url: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise BackendError(f"响应格式异常（{url}）", payload=payload)
        items_raw = payload.get("data") or payload.get("images") or []
        if isinstance(items_raw, dict):
            items_raw = [items_raw]
        items: list[dict[str, Any]] = []
        for entry in items_raw:
            if isinstance(entry, str):
                entry = {"url": entry}
            if not isinstance(entry, dict):
                continue
            b64 = entry.get("b64_json") or entry.get("base64") or entry.get("image_base64")
            if b64:
                try:
                    raw = base64.b64decode(str(b64))
                except Exception as exc:  # noqa: BLE001
                    raise BackendError(f"base64 解码失败: {exc}") from exc
                items.append(self.image_item(data=raw, mime=entry.get("mime") or "image/png"))
                continue
            link = entry.get("url") or entry.get("image_url") or entry.get("image")
            if isinstance(link, dict):
                link = link.get("url")
            if link:
                items.append(self.image_item(url=str(link), mime=entry.get("mime") or ""))
        if not items:
            raise BackendError(
                f"上游未返回图片数据（{url}）: {str(payload)[:200]}", payload=payload
            )
        return self.result(items, usage=payload.get("usage"), raw=payload)

    async def test(self) -> dict[str, Any]:
        base = self.test_base()
        problems: list[str] = []
        if not base:
            problems.append("base_url 为空")
        if not (self.provider.get("api_keys") or []):
            problems.append("未配置 API Key")
        if not self.model_id:
            problems.append("model_id 为空")
        if problems:
            return {"ok": False, "protocol": self.protocol, "problems": problems}
        result: dict[str, Any] = {
            "ok": True,
            "protocol": self.protocol,
            "base": base,
            "edit_mode": self.edit_mode,
            "note": "仅校验配置，未消耗额度",
        }
        return result

    def test_base(self) -> str:
        try:
            return self.base
        except BackendError:
            return ""
