"""可灵（Kling）图片与视频协议。

鉴权特殊：不是静态 Bearer，而是用 AK/SK 现签一个 HS256 JWT。
所以 api_keys 里每项按 `AccessKey:SecretKey` 填；只填一个串时视作现成 token。

图片：POST /v1/images/generations            → data.task_id
查询：GET  /v1/images/generations/{task_id}  → data.task_status / task_result.images[].url
视频：POST /v1/videos/text2video|image2video → data.task_id
查询：GET  /v1/videos/{type}/{task_id}       → data.task_result.videos[].url

上游错误全走 `code`（0 成功），HTTP 可能仍 200。
"""

from __future__ import annotations

import re
from typing import Any

from ..http import BackendError
from ._util import data_url, jwt_hs256, pick, poll_until, ratio_from_size, snap_ratio
from .base import CAP_I2I, CAP_T2I, CAP_T2V, Backend

_VERSION_RE = re.compile(r"/v\d+[a-z]*/?$", re.IGNORECASE)

_IMAGE_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"]
_VIDEO_RATIOS = ["16:9", "9:16", "1:1"]
_DONE = {"succeed", "succeeded", "success", "completed"}
_FAIL = {"failed", "fail", "error"}


# 可灵业务码 → HTTP 状态。
# 状态码直接决定「换不换家、计不计冷却」：400 在调度里既不回退也不冷却，
# 所以额度耗尽（1101/1102）与限流（1302/1303/1304）必须映射成 429，
# 否则用户额度用完后会卡在这家反复失败。依据官方错误码表。
_KLING_STATUS = {
    1000: 401, 1001: 401, 1002: 401, 1003: 401, 1004: 401,  # 鉴权失败
    1100: 429, 1101: 429, 1102: 429,                        # 账户异常 / 欠费 / 资源包耗尽
    1103: 403,                                              # 资源无权限
    1200: 400, 1201: 400,                                   # 参数非法
    1202: 404, 1203: 404,                                   # 方法 / 资源不存在
    1300: 400, 1301: 400,                                   # 平台 / 内容策略
    1302: 429, 1303: 429, 1304: 429,                        # 限流 / 并发超限 / IP 白名单
    5000: 500,                                              # 内部错误
    5001: 503, 5002: 503,                                   # 维护 / 积压超时，换家更稳
}


def kling_status_for(code: int) -> int:
    return _KLING_STATUS.get(int(code or 0), 400)


class KlingBackend(Backend):
    protocol = "kling"
    label = "可灵 Kling（图片 / 视频）"
    capabilities = (CAP_T2I, CAP_I2I, CAP_T2V)
    protocol_limits = {"min_duration": 5, "max_duration": 10}
    # duration_options：允许的时长档位，默认按协议限制吸附到 5/10
    extra_param_names = ("duration_options",)
    param_schema = {
        "negative_prompt": {"type": "string", "label": "反向提示词", "advanced": True},
        "mode": {
            "type": "enum",
            "label": "生成模式",
            "options": ["std", "pro"],
            "default": "std",
            "advanced": True,
        },
        "cfg_scale": {"type": "float", "label": "CFG 强度", "advanced": True},
        "image_fidelity": {"type": "float", "label": "参考图贴合度", "advanced": True},
        "human_fidelity": {"type": "float", "label": "人物一致性", "advanced": True},
        "resolution": {
            "type": "enum",
            "label": "分辨率档位",
            "options": ["1k", "2k"],
            "advanced": True,
        },
    }

    @property
    def base(self) -> str:
        base = str(self.provider.get("base_url") or "https://api.klingai.com").strip().rstrip("/")
        if not re.match(r"^https?://", base, re.IGNORECASE):
            base = "https://" + base
        return _VERSION_RE.sub("", base).rstrip("/")

    # ---------- 鉴权 ----------

    def auth(self) -> dict[str, str]:
        key = self.api_key()
        if not key:
            raise BackendError(f"供应商「{self.provider_name}」未配置 API Key", status=401)
        token = key
        if ":" in key or "|" in key:
            separator = ":" if ":" in key else "|"
            access, _, secret = key.partition(separator)
            if access.strip() and secret.strip():
                token = jwt_hs256(access.strip(), secret.strip())
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _snap_duration(value: int, params: dict[str, Any]) -> int:
        """可灵只接受固定时长档位（默认 5 / 10），传 8 秒会直接 400，这里吸附到最近档。"""
        raw = params.get("duration_options") or (5, 10)
        choices: list[int] = []
        for item in raw if isinstance(raw, (list, tuple, set)) else (raw,):
            try:
                number = int(item)
            except (TypeError, ValueError):
                continue
            if number > 0 and number not in choices:
                choices.append(number)
        if not choices:
            return int(value)
        return min(choices, key=lambda item: abs(item - int(value)))

    def _check(self, payload: Any, action: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise BackendError(f"可灵 {action} 返回格式异常", payload=payload)
        code = int(pick(payload, "code", default=0) or 0)
        if code:
            message = str(pick(payload, "message", "msg", default="") or "")
            raise BackendError(
                f"可灵 {action} 失败 [{code}] {message}",
                status=kling_status_for(code),
                payload=payload,
            )
        return payload

    async def _wait(self, url: str, *, timeout: float) -> dict[str, Any]:
        async def _fetch() -> dict[str, Any]:
            response = await self.http.get(url, headers=self.auth())
            return self._check(response.json(), "查询任务")

        return await poll_until(
            _fetch,
            is_done=lambda data: str((data.get("data") or {}).get("task_status") or "").lower()
            in _DONE,
            failure=lambda data: (
                str((data.get("data") or {}).get("task_status_message") or "failed")
                if str((data.get("data") or {}).get("task_status") or "").lower() in _FAIL
                else None
            ),
            timeout=timeout,
            interval=5.0,
            max_interval=15.0,
        )

    # ---------- 图片 ----------

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
        explicit_ratio = str(merged.pop("aspect_ratio", "") or aspect_ratio or "").strip()
        ratio = (
            snap_ratio(explicit_ratio, _IMAGE_RATIOS, "1:1")
            if explicit_ratio
            else ratio_from_size(size, allowed=_IMAGE_RATIOS) or "1:1"
        )

        body: dict[str, Any] = {"model_name": self.model_id, "prompt": prompt, "aspect_ratio": ratio}
        if images:
            body["image"] = data_url(images[0])
        for field, value in merged.items():
            if field in {"mode", "duration"}:
                continue
            if value is None or value == "":
                continue
            body.setdefault(field, value)

        created = await self.http.post(
            f"{self.base}/v1/images/generations", json_body=body, headers=self.auth()
        )
        payload = self._check(created.json(), "创建图片任务")
        task_id = pick(payload.get("data") or {}, "task_id")
        if not task_id:
            raise BackendError(
                f"可灵未返回 task_id: {str(payload)[:200]}", payload=payload
            )

        final = await self._wait(
            f"{self.base}/v1/images/generations/{task_id}",
            timeout=float(self.provider.get("poll_timeout") or 600),
        )
        result = (final.get("data") or {}).get("task_result") or {}
        items = [
            self.image_item(url=str(entry.get("url")))
            for entry in (result.get("images") or [])
            if isinstance(entry, dict) and entry.get("url")
        ]
        if not items:
            raise BackendError(
                f"可灵任务成功但没有图片: {str(final)[:200]}", payload=final
            )
        return self.result(items, task_id=str(task_id), raw=final)

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
        explicit_ratio = str(merged.pop("aspect_ratio", "") or aspect_ratio or "").strip()
        ratio = (
            snap_ratio(explicit_ratio, _VIDEO_RATIOS, "16:9")
            if explicit_ratio
            else ratio_from_size(size, allowed=_VIDEO_RATIOS) or "16:9"
        )

        body: dict[str, Any] = {"model_name": self.model_id, "prompt": prompt, "aspect_ratio": ratio}
        if duration:
            body["duration"] = self._snap_duration(int(duration), merged)
        kind = "text2video"
        if images:
            kind = "image2video"
            body["image"] = data_url(images[0])
        for field, value in merged.items():
            if field == "image" and images:
                continue
            # resolution / *_fidelity 只属于图片接口，塞进视频会 400
            if field in {"resolution", "image_fidelity", "human_fidelity", "n"}:
                continue
            if value is None or value == "":
                continue
            body.setdefault(field, value)

        created = await self.http.post(
            f"{self.base}/v1/videos/{kind}", json_body=body, headers=self.auth()
        )
        payload = self._check(created.json(), "创建视频任务")
        task_id = pick(payload.get("data") or {}, "task_id")
        if not task_id:
            raise BackendError(
                f"可灵未返回 task_id: {str(payload)[:200]}", payload=payload
            )

        final = await self._wait(
            f"{self.base}/v1/videos/{kind}/{task_id}",
            timeout=float(self.provider.get("poll_timeout") or 1800),
        )
        result = (final.get("data") or {}).get("task_result") or {}
        videos = result.get("videos") or []
        items = [
            self.video_item(
                url=str(entry.get("url")),
                duration=int(pick(entry, "duration", default=duration) or 0),
            )
            for entry in videos
            if isinstance(entry, dict) and entry.get("url")
        ]
        if not items:
            raise BackendError(
                f"可灵任务成功但没有视频: {str(final)[:200]}", payload=final
            )
        return self.result(items, task_id=str(task_id), raw=final)
