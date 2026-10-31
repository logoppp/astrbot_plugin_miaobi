"""火山方舟（Ark）视频生成协议 · Seedance 系列。

异步两段式：
    POST /api/v3/contents/generations/tasks      → {id}
    GET  /api/v3/contents/generations/tasks/{id} → {status, content:{video_url}}

参数走 `ratio` + `resolution` + `duration`，不吃精确宽高，
所以这里把 sizing 算出的尺寸反推成最接近的标准比例。
"""

from __future__ import annotations

from typing import Any

from ..http import BackendError
from ._util import poll_until, pick, ratio_from_size
from .ark_image import normalize_ark_base
from .base import CAP_T2V, Backend

_ALLOWED_RATIOS = ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "9:21"]


class ArkVideoBackend(Backend):
    protocol = "ark_video"
    label = "火山方舟 · 豆包视频"
    capabilities = (CAP_T2V,)
    protocol_limits = {"min_duration": 4, "max_duration": 15}
    param_schema = {
        "resolution": {
            "type": "enum",
            "label": "分辨率",
            "options": ["480p", "720p", "1080p"],
            "default": "720p",
        },
        "ratio": {
            "type": "enum",
            "label": "画面比例",
            "options": _ALLOWED_RATIOS,
            "advanced": True,
        },
        "generate_audio": {"type": "bool", "label": "生成同期声", "default": False},
        "watermark": {"type": "bool", "label": "添加水印", "default": False},
        "camera_fixed": {"type": "bool", "label": "固定镜头", "default": False},
        "seed": {"type": "int", "label": "随机种子", "advanced": True},
    }

    @property
    def base(self) -> str:
        base = normalize_ark_base(self.provider.get("base_url") or "")
        return base

    models_supported = True
    models_note = "方舟返回的是平台全量清单，不代表已开通；需要先在控制台开通 Seedance 才能调用。"

    def models_url(self) -> str:
        return f"{self.base}/api/v3/models"

    @property
    def task_url(self) -> str:
        return f"{self.base}/api/v3/contents/generations/tasks"

    # ---------- 构造请求 ----------

    @staticmethod
    def _resolution_from(size: str, params: dict[str, Any]) -> str:
        explicit = str(params.get("resolution") or "").strip().lower()
        if explicit:
            return explicit if explicit.endswith("p") else f"{explicit}p"
        text = str(size or "").lower()
        if "x" in text:
            try:
                height = int(text.partition("x")[2])
            except ValueError:
                height = 0
            if height:
                if height <= 540:
                    return "480p"
                if height <= 800:
                    return "720p"
                return "1080p"
        return "720p"

    @staticmethod
    def _ratio_from(size: str, aspect_ratio: str, params: dict[str, Any]) -> str:
        explicit = str(params.get("ratio") or aspect_ratio or "").strip()
        if explicit:
            from ._util import snap_ratio

            return snap_ratio(explicit, _ALLOWED_RATIOS, "16:9")
        return ratio_from_size(size, allowed=_ALLOWED_RATIOS) or "16:9"

    def _content(self, prompt: str, images: list[bytes] | None) -> list[dict[str, Any]]:
        from ._util import data_url

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for data in images or []:
            # 不写 role：缺省即首帧，Seedance 1.0 与 2.x 都认
            content.append({"type": "image_url", "image_url": {"url": data_url(data)}})
        return content

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
        watermark = merged.get("watermark")
        body: dict[str, Any] = {
            "model": self.model_id,
            "content": self._content(prompt, images),
            "ratio": self._ratio_from(size, aspect_ratio, merged),
            "resolution": self._resolution_from(size, merged),
            # 默认关水印，显式传了以用户为准
            "watermark": bool(watermark) if watermark is not None else False,
        }
        if duration:
            body["duration"] = int(duration)
        for key, value in merged.items():
            if value is None or value == "":
                continue
            body.setdefault(key, value)
        # 上面已用解析结果覆盖，避免被模型 params 里的脏值盖回去
        body["ratio"] = self._ratio_from(size, aspect_ratio, merged)
        body["resolution"] = self._resolution_from(size, merged)

        created = await self.http.post(
            self.task_url, json_body=body, headers=self.auth_headers()
        )
        payload = created.json()
        task_id = pick(payload, "id", "task_id", "data")
        if isinstance(task_id, dict):
            task_id = pick(task_id, "id", "task_id")
        if not task_id:
            raise BackendError(f"方舟未返回任务 ID: {str(payload)[:200]}", payload=payload)

        timeout = float(self.provider.get("poll_timeout") or 900)

        async def _fetch() -> dict[str, Any]:
            response = await self.http.get(
                f"{self.task_url}/{task_id}", headers=self.auth_headers()
            )
            return response.json()

        final = await poll_until(
            _fetch,
            is_done=lambda data: str(data.get("status") or "").lower()
            in {"succeeded", "success", "completed"},
            failure=lambda data: (
                str(pick(data, "error", "message", default="") or "")
                if str(data.get("status") or "").lower() in {"failed", "cancelled", "canceled"}
                else None
            ),
            timeout=timeout,
            interval=5.0,
            max_interval=15.0,
        )

        content = final.get("content") or {}
        link = pick(content, "video_url", "url", "file_url")
        if not link:
            raise BackendError(
                f"任务成功但没有视频地址: {str(final)[:200]}", payload=final
            )
        item = self.video_item(url=str(link), duration=int(pick(final, "duration", default=duration) or 0))
        return self.result(
            [item],
            usage=final.get("usage"),
            task_id=str(task_id),
            resolution=pick(final, "resolution"),
            raw=final,
        )
