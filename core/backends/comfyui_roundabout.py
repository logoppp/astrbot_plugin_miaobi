"""ComfyUI Roundabout 协议（独立协议，不与通用 OpenAI 图片合并）。

它兼容 OpenAI 的 /v1/images/generations 与 /v1/videos，但额外支持一批
工作流私有参数：二采放大倍率、分辨率节点、ResolutionSelector 档位、workflow_overrides。
这些参数只在这个协议里生效。
"""

from __future__ import annotations

import asyncio
import base64
import re
from typing import Any

from ..http import BackendError
from .base import CAP_I2I, CAP_T2I, CAP_T2V, Backend

_ENDPOINT_RE = re.compile(r"/(?:v1/)?(?:images/(?:generations|edits?)|videos)/?$", re.IGNORECASE)

# Roundabout ResolutionSelector 的标准比例标签；可在模型 params.selector_labels 覆盖
_SELECTOR_LABELS = {
    "1:1": "1:1 (Square)",
    "4:3": "4:3 (Standard)",
    "3:4": "3:4 (Portrait)",
    "16:9": "16:9 (Widescreen)",
    "9:16": "9:16 (Portrait Widescreen)",
    "3:2": "3:2 (Standard)",
    "2:3": "2:3 (Portrait)",
    "21:9": "21:9 (Ultrawide)",
}


def normalize_base(url: str) -> str:
    base = str(url or "").strip().rstrip("/")
    if not base:
        return ""
    return _ENDPOINT_RE.sub("", base).rstrip("/")


class RoundaboutBackend(Backend):
    protocol = "comfyui_roundabout"
    label = "ComfyUI Roundabout"
    capabilities = (CAP_T2I, CAP_I2I, CAP_T2V)
    protocol_limits = {"min_side": 256, "max_side": 4096, "align": 8}
    # selector_labels：分辨率选择器节点上的档位文案映射
    extra_param_names = ("selector_labels",)
    param_schema = {
        "megapixels": {"type": "float", "label": "目标百万像素", "default": 0.0},
        "aspect_ratio": {"type": "string", "label": "长宽比", "default": ""},
        "multiple": {"type": "int", "label": "对齐倍数", "default": 32},
        "resolution_selector_node": {"type": "string", "label": "分辨率选择节点 ID"},
        "resolution_nodes": {"type": "object", "label": "分辨率节点 {width,height}"},
        "workflow_upscale": {"type": "float", "label": "工作流内放大倍率", "default": 1.0},
        "workflow_overrides": {"type": "object", "label": "工作流覆写", "advanced": True},
    }

    @property
    def base(self) -> str:
        base = normalize_base(self.provider.get("base_url") or "")
        if not base:
            raise BackendError(f"供应商「{self.provider_name}」未配置 base_url")
        return base

    # ---------- 分辨率映射 ----------

    def _resolution_fields(self, size: str, params: dict[str, Any]) -> dict[str, Any]:
        """把目标尺寸翻译成 Roundabout 认的字段组合。"""
        try:
            width, height = (int(x) for x in str(size).lower().split("x", 1))
        except (TypeError, ValueError):
            return {}
        multiple = max(1, int(params.get("multiple") or 32))
        upscale = float(params.get("workflow_upscale") or 1.0)
        if upscale < 1.0:
            upscale = 1.0
        target_w, target_h = width, height
        if upscale > 1.0:
            width = max(multiple, int(round(width / upscale / multiple)) * multiple)
            height = max(multiple, int(round(height / upscale / multiple)) * multiple)

        overrides = dict(params.get("workflow_overrides") or {})
        nodes = params.get("resolution_nodes") or {}
        if isinstance(nodes, dict) and nodes.get("width") and nodes.get("height"):
            overrides[f"{nodes['width']}.inputs.value"] = width
            overrides[f"{nodes['height']}.inputs.value"] = height

        selector = str(params.get("resolution_selector_node") or "").strip()
        if selector:
            labels = params.get("selector_labels") or _SELECTOR_LABELS
            ratio = _nearest_ratio(width, height, list(labels))
            label = labels.get(ratio) or ratio
            megapixels = (width * height) / (1024 * 1024)
            overrides[f"{selector}.inputs.aspect_ratio"] = label
            overrides[f"{selector}.inputs.megapixels"] = round(megapixels, 4)
            overrides[f"{selector}.inputs.multiple"] = multiple

        fields: dict[str, Any] = {"size": f"{target_w}x{target_h}"}
        if overrides:
            fields["workflow_overrides"] = overrides
        if selector:
            # selector 的 megapixels 取「输入尺寸的像素量」，已被 upscale 除过
            fields["megapixels"] = round((width * height) / (1024 * 1024), 4)
        elif params.get("megapixels"):
            fields["megapixels"] = float(params["megapixels"])
        if params.get("aspect_ratio"):
            fields["aspect_ratio"] = str(params["aspect_ratio"])
        if params.get("multiple"):
            fields["multiple"] = multiple
        return fields

    # ---------- 文生图 / 图生图 ----------

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
        url = f"{self.base}/v1/images/generations"
        body: dict[str, Any] = {"model": self.model_id, "prompt": prompt}
        body.update(self._resolution_fields(size, merged))
        if images:
            data_urls = [
                "data:image/png;base64," + base64.b64encode(item).decode("ascii")
                for item in images
            ]
            body["image"] = data_urls[0] if len(data_urls) == 1 else data_urls
        for key, value in merged.items():
            if key in _INTERNAL_KEYS or value in (None, ""):
                continue
            body.setdefault(key, value)

        response = await self.http.post(url, json_body=body, headers=self.auth_headers())
        payload = response.json()
        items = []
        for entry in payload.get("data") or []:
            if isinstance(entry, str):
                entry = {"url": entry}
            b64 = entry.get("b64_json")
            if b64:
                items.append(self.image_item(data=base64.b64decode(b64)))
            elif entry.get("url"):
                items.append(self.image_item(url=str(entry["url"])))
        if not items:
            raise BackendError(f"Roundabout 未返回图片: {str(payload)[:200]}", payload=payload)
        return self.result(items, usage=payload.get("usage"), raw=payload)

    # ---------- 视频 ----------

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
        url = f"{self.base}/v1/videos"
        body: dict[str, Any] = {"model": self.model_id, "prompt": prompt}
        if duration:
            body["seconds"] = str(duration)
        body.update(self._resolution_fields(size, merged))
        for key, value in merged.items():
            if key in _INTERNAL_KEYS or value in (None, ""):
                continue
            body.setdefault(key, value)

        response = await self.http.post(url, json_body=body, headers=self.auth_headers())
        payload = response.json()

        video_url = _extract_video_url(payload)
        if video_url:
            return self.result(
                [self.video_item(url=video_url, duration=duration)], raw=payload
            )

        task_id = payload.get("id") or payload.get("task_id") or payload.get("video_id")
        if not task_id:
            raise BackendError(f"Roundabout 视频响应缺少任务 id: {str(payload)[:200]}", payload=payload)

        final = await self._poll_video(str(task_id), timeout=int(self.provider.get("poll_timeout") or 900))
        link = _extract_video_url(final)
        if not link:
            raise BackendError(f"视频任务完成但无下载地址: {str(final)[:200]}", payload=final)
        return self.result([self.video_item(url=link, duration=duration)], raw=final)

    async def _poll_video(self, task_id: str, *, timeout: int) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        url = f"{self.base}/v1/videos/{task_id}"
        delay = 3.0
        while True:
            if asyncio.get_running_loop().time() > deadline:
                raise BackendError(f"视频任务超时（{timeout}s）: {task_id}", status=504)
            response = await self.http.get(url, headers=self.auth_headers())
            payload = response.json()
            status = str(payload.get("status") or "").lower()
            if status in ("completed", "succeeded", "success", "done"):
                return payload
            if status in ("failed", "error", "cancelled"):
                raise BackendError(
                    f"视频任务失败: {payload.get('error') or str(payload)[:200]}", payload=payload
                )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 15.0)

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


_INTERNAL_KEYS = {
    "megapixels",
    "aspect_ratio",
    "multiple",
    "workflow_overrides",
    "resolution_selector_node",
    "resolution_nodes",
    "workflow_upscale",
    "selector_labels",
    "async_video",
}


def _nearest_ratio(width: int, height: int, choices: list[str]) -> str:
    import math

    target = width / max(height, 1)
    best = choices[0] if choices else "1:1"
    best_delta = None
    for item in choices:
        try:
            left, right = (float(x) for x in item.split(":", 1))
        except (TypeError, ValueError):
            continue
        value = left / max(right, 1e-6)
        delta = abs(math.log(value / max(target, 1e-6)))
        if best_delta is None or delta < best_delta:
            best, best_delta = item, delta
    return best


def _extract_video_url(payload: dict[str, Any]) -> str:
    for key in ("url", "video_url", "output_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    data = payload.get("data")
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                link = entry.get("url") or entry.get("video_url")
                if isinstance(link, str) and link.startswith("http"):
                    return link
    output = payload.get("output")
    if isinstance(output, dict):
        link = output.get("url") or output.get("video_url")
        if isinstance(link, str) and link.startswith("http"):
            return link
    return ""
