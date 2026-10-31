"""MiniMax（海螺 / Hailuo）视频生成协议。

三段式：
    POST /v1/video_generation?              → {task_id, base_resp}
    GET  /v1/query/video_generation?task_id → {status, file_id}
    GET  /v1/files/retrieve?file_id         → {file:{download_url}}

注意上游错误走 `base_resp.status_code`，HTTP 可能仍是 200，必须自己判。
"""

from __future__ import annotations

import re
from typing import Any

from ..http import BackendError
from ._util import data_url, pick, poll_until
from .base import CAP_T2V, Backend

_VERSION_RE = re.compile(r"/v\d+[a-z]*/?$", re.IGNORECASE)

_OK_STATUS = {"success", "succeeded"}
_FAIL_STATUS = {"fail", "failed", "error"}
_ALLOWED_RESOLUTIONS = ["720P", "768P", "1080P"]


def normalize_native_base(url: str, default: str) -> str:
    base = str(url or "").strip().rstrip("/")
    if not base:
        return default
    if not re.match(r"^https?://", base, re.IGNORECASE):
        base = "https://" + base
    return _VERSION_RE.sub("", base).rstrip("/")


class MiniMaxVideoBackend(Backend):
    protocol = "minimax_video"
    label = "MiniMax · 海螺视频"
    capabilities = (CAP_T2V,)
    protocol_limits = {"min_duration": 5, "max_duration": 10}
    param_schema = {
        "resolution": {
            "type": "enum",
            "label": "分辨率",
            "options": _ALLOWED_RESOLUTIONS,
            "default": "768P",
        },
        "prompt_optimizer": {"type": "bool", "label": "自动优化提示词", "default": True},
        "aigc_watermark": {"type": "bool", "label": "添加水印", "default": False},
        "fast_pretreatment": {"type": "bool", "label": "极速预处理", "default": False},
        "first_frame_image": {
            "type": "string",
            "label": "首帧图 URL（留空则用上传图）",
            "advanced": True,
        },
    }

    @property
    def base(self) -> str:
        return normalize_native_base(
            self.provider.get("base_url") or "", "https://api.minimax.chat"
        )

    def _check(self, payload: Any, action: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise BackendError(f"MiniMax {action} 返回格式异常", payload=payload)
        base_resp = payload.get("base_resp") or {}
        code = int(pick(base_resp, "status_code", default=0) or 0)
        if code:
            message = str(pick(base_resp, "status_msg", default="") or "")
            raise BackendError(
                f"MiniMax {action} 失败 [{code}] {message}",
                status=401 if code in (1004, 2049) else 429 if code == 1002 else 400,
                payload=payload,
            )
        return payload

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
        body: dict[str, Any] = {"model": self.model_id, "prompt": prompt}
        if duration:
            body["duration"] = int(duration)

        first = str(merged.get("first_frame_image") or "").strip()
        if not first and images:
            first = data_url(images[0])
        if first:
            body["first_frame_image"] = first

        for key, value in merged.items():
            if key == "first_frame_image" or value is None or value == "":
                continue
            body.setdefault(key, value)
        body.setdefault("prompt_optimizer", True)
        body.setdefault("aigc_watermark", False)

        headers = self.auth_headers()
        created = await self.http.post(
            f"{self.base}/v1/video_generation", json_body=body, headers=headers
        )
        payload = self._check(created.json(), "创建任务")
        task_id = pick(payload, "task_id", "id")
        if not task_id:
            raise BackendError(f"MiniMax 未返回 task_id: {str(payload)[:200]}", payload=payload)

        timeout = float(self.provider.get("poll_timeout") or 900)

        async def _query() -> dict[str, Any]:
            response = await self.http.get(
                f"{self.base}/v1/query/video_generation",
                params={"task_id": task_id},
                headers=self.auth_headers(),
            )
            return self._check(response.json(), "查询任务")

        final = await poll_until(
            _query,
            is_done=lambda data: str(data.get("status") or "").lower() in _OK_STATUS,
            failure=lambda data: (
                f"{data.get('status')}"
                if str(data.get("status") or "").lower() in _FAIL_STATUS
                else None
            ),
            timeout=timeout,
            interval=6.0,
            max_interval=15.0,
        )

        file_id = pick(final, "file_id")
        link = ""
        if file_id:
            retrieved = await self.http.get(
                f"{self.base}/v1/files/retrieve",
                params={"file_id": file_id},
                headers=self.auth_headers(),
            )
            info = self._check(retrieved.json(), "获取文件")
            file_obj = info.get("file") or {}
            link = str(pick(file_obj, "download_url", "url", default="") or "")
        if not link:
            link = str(pick(final, "download_url", "video_url", default="") or "")
        if not link:
            raise BackendError(
                f"任务成功但没有下载地址: {str(final)[:200]}", payload=final
            )

        item = self.video_item(
            url=link,
            duration=int(pick(final, "duration", default=duration) or 0),
        )
        return self.result(
            [item],
            task_id=str(task_id),
            width=int(pick(final, "video_width", default=0) or 0),
            height=int(pick(final, "video_height", default=0) or 0),
            raw=final,
        )
