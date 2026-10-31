"""火山方舟（Ark）图片生成协议。

覆盖 doubao-seedream-4.x / 5.x 与 seededit 系列。
文生图与图生图共用 `/api/v3/images/generations`，只是多一个 `image` 字段
（支持单图字符串或多图数组，元素可为 http URL，也可为 data URL）。

`size` 既接受 `1K/2K/4K` 这类档位，也接受显式 `宽x高`；我们统一传显式尺寸，
好让上游按精确比例出图，再由 sizing 层保证不越界。
"""

from __future__ import annotations

import re
from typing import Any

from ..http import BackendError
from ._util import data_url, decode_b64, pick
from .base import CAP_I2I, CAP_T2I, Backend

_ARK_VERSION_RE = re.compile(r"/api/v\d+[a-z]*/?$", re.IGNORECASE)


def normalize_ark_base(url: str) -> str:
    """收敛成「可直接拼 /api/v3/images/generations」的根域名。"""
    base = str(url or "").strip().rstrip("/")
    if not base:
        return "https://ark.cn-beijing.volces.com"
    base = _ARK_VERSION_RE.sub("", base).rstrip("/")
    if base.endswith("/api"):
        base = base[:-4].rstrip("/")
    if not re.match(r"^https?://", base, re.IGNORECASE):
        base = "https://" + base
    return base


class ArkImageBackend(Backend):
    protocol = "ark_image"
    label = "火山方舟 · 豆包图片"
    capabilities = (CAP_T2I, CAP_I2I)
    # 官方：单边 > 14px，比例 [1/16, 16]，总面积上限 4096x4096
    protocol_limits = {
        "min_side": 512,
        "max_side": 4096,
        "min_pixels": 1280 * 720,
        "max_pixels": 4096 * 4096,
        "align": 8,
    }
    param_schema = {
        "watermark": {"type": "bool", "label": "添加 AI 水印", "default": False},
        "sequential_image_generation": {
            "type": "enum",
            "label": "组图模式",
            "options": ["disabled", "auto"],
            "default": "disabled",
        },
        "max_images": {"type": "int", "label": "组图上限", "min": 1, "max": 15, "advanced": True},
        "seed": {"type": "int", "label": "随机种子", "advanced": True},
        "guidance_scale": {"type": "float", "label": "CFG 强度", "advanced": True},
        "output_format": {
            "type": "enum",
            "label": "输出格式",
            "options": ["jpeg", "png"],
            "advanced": True,
        },
    }

    models_supported = True
    models_note = "方舟返回的是平台全量清单，不代表你的账号已开通；未开通的模型调用会报 InvalidEndpointOrModel.NotFound。"

    @property
    def base(self) -> str:
        return normalize_ark_base(self.provider.get("base_url") or "")

    @property
    def endpoint(self) -> str:
        return f"{self.base}/api/v3/images/generations"

    def models_url(self) -> str:
        return f"{self.base}/api/v3/models"

    # ---------- 请求 ----------

    def _images_payload(self, images: list[bytes] | None) -> Any:
        if not images:
            return None
        urls = [data_url(data) for data in images]
        return urls[0] if len(urls) == 1 else urls

    def _body(
        self, prompt: str, size: str, images: list[bytes] | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model_id,
            "prompt": prompt,
            "response_format": "url",
        }
        # 官方默认 true，用户要的是干净图；显式传了就以用户为准（原先写死后 setdefault 覆盖不了）
        watermark = (params or {}).get("watermark")
        body["watermark"] = bool(watermark) if watermark is not None else False
        if size:
            body["size"] = size
        refs = self._images_payload(images)
        if refs:
            body["image"] = refs

        for key, value in (params or {}).items():
            if value is None or value == "":
                continue
            if key == "max_images":
                body.setdefault("sequential_image_generation_options", {})["max_images"] = int(value)
                continue
            body.setdefault(key, value)

        # 组图必须显式给出上限，否则部分型号会退化成单图
        if body.get("sequential_image_generation") == "auto":
            body.setdefault("sequential_image_generation_options", {"max_images": 4})
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

    # ---------- 响应 ----------

    def _parse(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise BackendError("方舟返回格式异常", payload=payload)
        error = payload.get("error")
        if isinstance(error, dict) and error:
            code = pick(error, "code")
            raise BackendError(
                f"方舟报错: {pick(error, 'message', 'code', default=str(error)[:200])}",
                status=code if isinstance(code, int) else 0,
                payload=payload,
            )
        entries = payload.get("data") or []
        if isinstance(entries, dict):
            entries = [entries]
        items: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            b64 = pick(entry, "b64_json", "base64")
            if b64:
                items.append(
                    self.image_item(
                        data=decode_b64(str(b64)), mime=pick(entry, "mime", default="image/jpeg")
                    )
                )
                continue
            link = pick(entry, "url", "image_url")
            if isinstance(link, dict):
                link = pick(link, "url")
            if link:
                items.append(self.image_item(url=str(link)))
        if not items:
            raise BackendError(
                f"方舟未返回图片: {str(payload)[:200]}", payload=payload
            )
        return self.result(items, usage=payload.get("usage"), raw=payload)
