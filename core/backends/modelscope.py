"""魔搭 ModelScope 图片生成协议。

两段式：POST /v1/images/generations 提交（必须带 X-ModelScope-Async-Mode: true），
拿到 task_id 后轮询 GET /v1/tasks/{id}（带 X-ModelScope-Task-Type: image_generation）。

`base_url` 填 `https://api-inference.modelscope.cn` 即可，贴到 /v1 也兼容。
模型名填魔搭的 Model-Id，例如 `Tongyi-MAI/Z-Image-Turbo`、`Qwen/Qwen-Image`。
"""

from __future__ import annotations

import re
from typing import Any

from ..http import BackendError
from ._util import pick, poll_until
from .base import CAP_T2I, Backend

_SIZE_RE = re.compile(r"^\s*(\d+)\s*[xX*×]\s*(\d+)\s*$")
_VERSION_RE = re.compile(r"/v1(/.*)?$", re.IGNORECASE)

DONE_STATUS = {"SUCCEED", "SUCCEEDED"}
FAIL_STATUS = {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN", "TIMEOUT"}


def normalize_modelscope_base(url: str) -> str:
    """收敛成可直接拼 /v1/images/generations 的根地址。"""
    base = str(url or "").strip().rstrip("/")
    if not base:
        return "https://api-inference.modelscope.cn"
    if not re.match(r"^https?://", base, re.IGNORECASE):
        base = "https://" + base
    base = _VERSION_RE.sub("", base).rstrip("/")
    return base


class ModelScopeBackend(Backend):
    protocol = "modelscope"
    label = "魔搭 ModelScope"
    # 当前只接文生图：API-Inference 的图生图入口各模型不统一，先不猜
    capabilities = (CAP_T2I,)
    protocol_limits = {
        "min_side": 512,
        "max_side": 2048,
        "min_pixels": 512 * 512,
        "max_pixels": 2048 * 2048,
        "align": 8,
    }
    param_schema = {
        "num_inference_steps": {
            "type": "int",
            "label": "采样步数",
            "min": 1,
            "max": 100,
            "advanced": True,
        },
        "guidance_scale": {"type": "float", "label": "CFG 强度", "advanced": True},
        "seed": {"type": "int", "label": "随机种子", "advanced": True},
        "negative_prompt": {"type": "string", "label": "负面提示词", "advanced": True},
    }

    models_supported = True
    models_note = "模型名要填魔搭的 Model-Id，如 Tongyi-MAI/Z-Image-Turbo；在魔搭模型页「推理 API-Inference」可直接复制。"

    @property
    def base(self) -> str:
        return normalize_modelscope_base(self.provider.get("base_url") or "")

    @property
    def endpoint(self) -> str:
        return f"{self.base}/v1/images/generations"

    def models_url(self) -> str:
        return f"{self.base}/v1/models"

    # ---------- 请求 ----------

    def _body(
        self, prompt: str, size: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model_id, "prompt": prompt}
        match = _SIZE_RE.match(str(size or ""))
        if match:
            body["width"] = int(match.group(1))
            body["height"] = int(match.group(2))
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
        if images:
            raise BackendError("魔搭协议目前只支持文生图", status=400)
        merged = self.merge_params(params)
        headers = {**self.auth_headers(), "X-ModelScope-Async-Mode": "true"}
        response = await self.http.post(
            self.endpoint, json_body=self._body(prompt, size, merged), headers=headers
        )
        payload = response.json()
        task_id = str(pick(payload, "task_id", "id", default="") or "")
        if not task_id:
            raise BackendError(
                f"魔搭未返回 task_id: {str(payload)[:200]}",
                status=response.status,
                payload=payload,
            )

        poll_headers = {
            **self.auth_headers(),
            "X-ModelScope-Task-Type": "image_generation",
        }
        url = f"{self.base}/v1/tasks/{task_id}"

        async def fetch() -> dict[str, Any]:
            resp = await self.http.get(url, headers=poll_headers)
            data = resp.json()
            return data if isinstance(data, dict) else {}

        def failed(data: dict[str, Any]) -> str | None:
            status = str(data.get("task_status") or "").upper()
            if status in FAIL_STATUS:
                detail = pick(data, "errors", "message", "error", default="")
                return f"{status} {detail}".strip()
            return None

        final = await poll_until(
            fetch,
            is_done=lambda data: str(data.get("task_status") or "").upper() in DONE_STATUS,
            failure=failed,
            timeout=float(self.provider.get("poll_timeout") or 900),
            interval=3.0,
        )

        links = final.get("output_images") or []
        if isinstance(links, str):
            links = [links]
        items = [self.image_item(url=str(link)) for link in links if link]
        if not items:
            raise BackendError(
                f"魔搭未返回图片: {str(final)[:200]}", payload=final
            )
        usage = pick(final, "usage", default=None)
        return self.result(items, usage=usage, raw=final)
