"""DashScope 原生多模态生成协议（万相 / z-image 等）。

与 OpenAI 兼容入口的区别：
- 尺寸用 `1024*1024`（星号），不是 `1024x1024`；
- 图生图把参考图放进 input.messages[].content[].image；
- 异步模式需要 `X-DashScope-Async: async` 头，再轮询 /api/v1/tasks/{id}。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..http import BackendError
from .base import CAP_I2I, CAP_T2I, Backend

_SUBMIT = "/api/v1/services/aigc/multimodal-generation/generation"
_TASK = "/api/v1/tasks/{task_id}"


class DashScopeNativeBackend(Backend):
    protocol = "dashscope_native"
    label = "DashScope 原生（万相 / z-image）"
    capabilities = (CAP_T2I, CAP_I2I)
    protocol_limits = {"min_side": 256, "max_side": 2048, "align": 8}
    param_schema = {
        "n": {"type": "int", "label": "生成数量", "default": 1, "min": 1, "max": 4},
        "negative_prompt": {"type": "string", "label": "反向提示词"},
        "prompt_extend": {"type": "bool", "label": "提示词扩写", "default": True},
        "watermark": {"type": "bool", "label": "水印", "default": False},
        "seed": {"type": "int", "label": "随机种子", "advanced": True},
        "async_mode": {"type": "bool", "label": "异步任务", "default": False},
    }

    models_supported = True
    models_note = "先试中转站的 OpenAI 兼容路由，再试百炼原生路由；两处都没有时会提示手动填写。"

    @property
    def base(self) -> str:
        base = str(self.provider.get("base_url") or "").strip().rstrip("/")
        if not base:
            raise BackendError(f"供应商「{self.provider_name}」未配置 base_url")
        for suffix in ("/compatible-mode/v1", "/api/v1"):
            if base.endswith(suffix):
                base = base[: -len(suffix)].rstrip("/")
        return base

    def models_urls(self) -> list[str]:
        return [f"{self.base}/compatible-mode/v1/models", f"{self.base}/api/v1/models"]

    def _content(self, prompt: str, images: list[bytes] | None) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"text": prompt}]
        for data in images or []:
            import base64

            content.append(
                {"image": "data:image/png;base64," + base64.b64encode(data).decode("ascii")}
            )
        return content

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
        async_mode = bool(merged.pop("async_mode", False))
        body: dict[str, Any] = {
            "model": self.model_id,
            "input": {"messages": [{"role": "user", "content": self._content(prompt, images)}]},
        }
        parameters: dict[str, Any] = {}
        if size:
            parameters["size"] = size.replace("x", "*")
        for key in ("n", "negative_prompt", "prompt_extend", "watermark", "seed", "steps"):
            if key in merged and merged[key] not in (None, ""):
                parameters[key] = merged[key]
        if parameters:
            body["parameters"] = parameters
        for key, value in merged.items():
            if value in (None, "") or key in parameters:
                continue
            body.setdefault(key, value)

        headers = self.auth_headers()
        if async_mode:
            headers["X-DashScope-Async"] = "async"

        response = await self.http.post(f"{self.base}{_SUBMIT}", json_body=body, headers=headers)
        payload = response.json()

        if async_mode:
            task_id = (payload.get("output") or {}).get("task_id") or payload.get("task_id")
            if not task_id:
                raise BackendError(f"异步提交未返回 task_id: {str(payload)[:200]}", payload=payload)
            payload = await self._poll(str(task_id))

        return self._parse(payload)

    async def _poll(self, task_id: str) -> dict[str, Any]:
        timeout = int(self.provider.get("poll_timeout") or 900)
        deadline = asyncio.get_running_loop().time() + timeout
        delay = 2.0
        while True:
            if asyncio.get_running_loop().time() > deadline:
                raise BackendError(f"DashScope 任务超时: {task_id}", status=504)
            response = await self.http.get(f"{self.base}{_TASK.format(task_id=task_id)}", headers=self.auth_headers())
            payload = response.json()
            output = payload.get("output") or {}
            status = str(output.get("task_status") or "").upper()
            if status in ("SUCCEEDED", "SUCCESS"):
                return payload
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                raise BackendError(f"DashScope 任务失败: {str(payload)[:240]}", payload=payload)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 10.0)

    def _parse(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = payload.get("output") or {}
        items: list[dict[str, Any]] = []
        for choice in output.get("choices") or []:
            message = choice.get("message") or {}
            for entry in message.get("content") or []:
                if not isinstance(entry, dict):
                    continue
                link = entry.get("image") or entry.get("url")
                if link:
                    items.append(self.image_item(url=str(link)))
        for entry in output.get("results") or []:
            if isinstance(entry, dict) and entry.get("url"):
                items.append(self.image_item(url=str(entry["url"])))
        if not items:
            raise BackendError(f"DashScope 未返回图片: {str(payload)[:240]}", payload=payload)
        return self.result(items, usage=payload.get("usage"), raw=payload)

    async def test(self) -> dict[str, Any]:
        problems: list[str] = []
        base = ""
        try:
            base = self.base
        except BackendError:
            problems.append("base_url 为空")
        if not (self.provider.get("api_keys") or []):
            problems.append("未配置 API Key")
        if not self.model_id:
            problems.append("model_id 为空")
        return {
            "ok": not problems,
            "protocol": self.protocol,
            "base": base,
            "problems": problems,
            "note": "仅校验配置，未消耗额度",
        }
