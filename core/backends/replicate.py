"""Replicate 协议。

两种调用形态，按 model_id 自动判断：
    owner/name      → POST /v1/models/{owner}/{name}/predictions   （官方托管模型）
    版本 hash       → POST /v1/predictions  {version, input}       （社区模型固定版本）

结果靠 `urls.get` 轮询，`output` 可能是字符串、字符串数组或对象数组，
这里统一摊平成 URL 列表，再交给 media 层下载。
"""

from __future__ import annotations

import re
from typing import Any

from ..http import BackendError
from ._util import pick, poll_until
from .base import CAP_I2I, CAP_T2I, CAP_T2V, Backend

_VERSION_RE = re.compile(r"/v\d+[a-z]*/?$", re.IGNORECASE)
_HASH_RE = re.compile(r"^[0-9a-f]{32,64}$", re.IGNORECASE)

_DONE = {"succeeded", "successful", "completed"}
_FAIL = {"failed", "canceled", "cancelled", "error"}


class ReplicateBackend(Backend):
    protocol = "replicate"
    label = "Replicate"
    capabilities = (CAP_T2I, CAP_I2I, CAP_T2V)
    # image_field：图生图参考图塞进哪个输入名，各模型不统一；duration 仅视频模型认
    extra_param_names = ("image_field", "duration")
    param_schema = {
        # 不放 negative_prompt：Replicate 的 input schema 由各模型自己定义，
        # Flux 这类严格校验的模型多一个字段就 422。确实需要时写在模型的 params 里，
        # 那条路径是用户显式配置，不会被白名单裁掉。
        "aspect_ratio": {"type": "string", "label": "画面比例", "advanced": True},
        "num_outputs": {"type": "int", "label": "生成数量", "min": 1, "max": 4, "advanced": True},
        "output_format": {
            "type": "enum",
            "label": "输出格式",
            "options": ["png", "jpg", "webp"],
            "advanced": True,
        },
        "seed": {"type": "int", "label": "随机种子", "advanced": True},
        "disable_safety_checker": {"type": "bool", "label": "关闭安全检查", "advanced": True},
    }

    @property
    def base(self) -> str:
        base = str(self.provider.get("base_url") or "https://api.replicate.com").strip().rstrip("/")
        if not re.match(r"^https?://", base, re.IGNORECASE):
            base = "https://" + base
        return _VERSION_RE.sub("", base).rstrip("/")

    models_supported = True
    models_note = "Replicate 公共模型库（只列官方收录的模型），私有部署不会出现在这里。"

    def models_url(self) -> str:
        return f"{self.base}/v1/models"

    def _endpoint(self) -> tuple[str, dict[str, Any]]:
        model = str(self.model_id or "").strip()
        if _HASH_RE.match(model):
            return f"{self.base}/v1/predictions", {"version": model}
        if "/" in model:
            return f"{self.base}/v1/models/{model}/predictions", {}
        raise BackendError(
            f"Replicate 模型名需为 owner/name 或版本 hash，当前是「{model}」"
        )

    def _input(
        self,
        prompt: str,
        size: str,
        aspect_ratio: str,
        images: list[bytes] | None,
        params: dict[str, Any],
        *,
        video: bool,
    ) -> dict[str, Any]:
        from ._util import data_url

        payload: dict[str, Any] = {"prompt": prompt}
        # 只发用户显式指定的比例：Replicate 对 input schema 是严格校验的，
        # 模型不认 aspect_ratio 时自动补一个会直接 422。
        ratio = str(params.get("aspect_ratio") or aspect_ratio or "").strip()
        if ratio:
            payload["aspect_ratio"] = ratio
        if video:
            payload.setdefault("duration", 5)
        if images:
            # 各模型字段名不同（image / input_image / init_image），默认 image，可按模型覆盖
            field = str(params.get("image_field") or "image").strip() or "image"
            payload[field] = data_url(images[0])
        for key, value in params.items():
            if key in {"image_field", "aspect_ratio"} or value is None or value == "":
                continue
            payload.setdefault(key, value)
        return payload

    async def _run(self, body: dict[str, Any], url: str) -> dict[str, Any]:
        headers = self.auth_headers()
        created = await self.http.post(url, json_body=body, headers=headers)
        payload = created.json()
        if not isinstance(payload, dict):
            raise BackendError("Replicate 返回格式异常", payload=payload)
        if payload.get("detail"):
            raise BackendError(f"Replicate 报错: {payload['detail']}", payload=payload)

        task_id = pick(payload, "id")
        status = str(pick(payload, "status", default="") or "").lower()
        if status in _DONE:
            return payload
        poll_url = pick(payload.get("urls") or {}, "get") or (
            f"{self.base}/v1/predictions/{task_id}" if task_id else ""
        )
        if not poll_url:
            raise BackendError(f"Replicate 未返回任务地址: {str(payload)[:200]}", payload=payload)

        async def _fetch() -> dict[str, Any]:
            response = await self.http.get(str(poll_url), headers=self.auth_headers())
            return response.json()

        return await poll_until(
            _fetch,
            is_done=lambda data: str(data.get("status") or "").lower() in _DONE,
            failure=lambda data: (
                str(pick(data, "error", default="failed") or "failed")
                if str(data.get("status") or "").lower() in _FAIL
                else None
            ),
            timeout=float(self.provider.get("poll_timeout") or 900),
            interval=3.0,
            max_interval=10.0,
        )

    @staticmethod
    def _urls(output: Any) -> list[str]:
        found: list[str] = []
        stack = [output]
        while stack:
            item = stack.pop(0)
            if isinstance(item, str):
                if item.startswith("http"):
                    found.append(item)
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                for key in ("url", "video", "image", "output"):
                    if key in item:
                        stack.append(item[key])
                if not found and len(item) <= 2:
                    stack.extend(item.values())
        # 去重保序
        seen: set[str] = set()
        ordered: list[str] = []
        for url in found:
            if url not in seen:
                seen.add(url)
                ordered.append(url)
        return ordered

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
        url, extra = self._endpoint()
        body = dict(extra)
        body["input"] = self._input(prompt, size, aspect_ratio, images, merged, video=False)
        final = await self._run(body, url)
        links = self._urls(final.get("output"))
        if not links:
            raise BackendError(
                f"Replicate 任务成功但没有输出: {str(final)[:200]}", payload=final
            )
        items = [self.image_item(url=link) for link in links]
        return self.result(items, task_id=pick(final, "id"), raw=final)

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
        merged = self.merge_params(params)
        url, extra = self._endpoint()
        body = dict(extra)
        payload = self._input(prompt, size, aspect_ratio, images, merged, video=True)
        if duration:
            payload["duration"] = int(duration)
        body["input"] = payload
        final = await self._run(body, url)
        links = self._urls(final.get("output"))
        if not links:
            raise BackendError(
                f"Replicate 任务成功但没有视频: {str(final)[:200]}", payload=final
            )
        items = [self.video_item(url=link, duration=duration) for link in links]
        return self.result(items, task_id=pick(final, "id"), raw=final)
