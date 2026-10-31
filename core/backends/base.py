"""妙笔 · 后端基类。

一个后端 = 一种「协议族」适配器（OpenAI Images、DashScope、NovelAI……），
由 provider.protocol 决定实例化哪个类，model 描述具体型号的能力与限制。
"""

from __future__ import annotations

import re
from typing import Any

from ..http import BackendError, HTTP

CAP_T2I = "t2i"
CAP_I2I = "i2i"
CAP_T2V = "t2v"

# 按供应商轮换 key；同一个后端实例（一次任务）内固定用同一把
_KEY_CURSOR: dict[str, int] = {}


class Backend:
    protocol = ""
    label = ""
    capabilities: tuple[str, ...] = ()
    # 协议层的硬限制，会和模型限制、全局限制取交集
    protocol_limits: dict[str, Any] = {}
    # 该协议支持哪些可调参数，供 WebUI 动态渲染；同时是参数白名单的来源
    param_schema: dict[str, Any] = {}
    # 不对外暴露、但后端会读的隐式参数，必须放进白名单，否则会被过滤掉
    extra_param_names: tuple[str, ...] = ()
    # 自定义模板这类协议吃的字段由用户模板决定，不做白名单
    accept_all_params = False
    # 需要 provider.edit_mode 区分图生图走法的协议
    supports_edit_mode = False

    def __init__(
        self,
        provider: dict[str, Any],
        model: dict[str, Any],
        *,
        media=None,
        settings: dict[str, Any] | None = None,
    ):
        self.provider = provider or {}
        self.model = model or {}
        self.media = media
        self.settings = settings or {}
        self.protocol_limits = dict(type(self).protocol_limits)
        self.http = HTTP(
            timeout=self.provider.get("timeout") or 600,
            proxy=self.provider.get("proxy"),
            retries=self.provider.get("max_retries", 1),
            headers=self.provider.get("headers") or {},
        )
        self._pinned_key: str | None = None

    # ---------- 基础信息 ----------

    @property
    def model_key(self) -> str:
        return str(self.model.get("id") or "")

    @property
    def model_id(self) -> str:
        return str(self.model.get("model_id") or self.model.get("id") or "")

    @property
    def model_label(self) -> str:
        return str(self.model.get("display_name") or self.model_id)

    @property
    def provider_id(self) -> str:
        return str(self.provider.get("id") or "")

    @property
    def provider_name(self) -> str:
        return str(self.provider.get("name") or self.provider_id)

    @property
    def capabilities_set(self) -> set[str]:
        return {c for c in (self.model.get("capabilities") or []) if c}

    def has(self, capability: str) -> bool:
        return capability in self.capabilities_set

    # ---------- 鉴权 ----------

    def api_key(self) -> str:
        """一次任务内固定用同一把 key。

        原实现每次调用都轮换：建任务用 A、轮询用 B，多 key 时轮询会直接鉴权失败。
        现在改成「按供应商跨任务轮换、任务内固定」。
        """
        if self._pinned_key is not None:
            return self._pinned_key
        keys = [
            str(k).strip() for k in (self.provider.get("api_keys") or []) if str(k).strip()
        ]
        if not keys:
            return ""
        cursor = _KEY_CURSOR.get(self.provider_id, 0)
        self._pinned_key = keys[cursor % len(keys)]
        _KEY_CURSOR[self.provider_id] = cursor + 1
        return self._pinned_key

    def auth_headers(self, *, scheme: str = "Bearer") -> dict[str, str]:
        key = self.api_key()
        if not key:
            raise BackendError(
                f"供应商「{self.provider_name}」未配置 API Key", status=401
            )
        return {"Authorization": f"{scheme} {key}" if scheme else key}

    # ---------- 调用 ----------

    async def generate_image(
        self,
        prompt: str,
        *,
        size: str = "",
        aspect_ratio: str = "",
        images: list[bytes] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise BackendError(f"{type(self).__name__} 未实现文生图")

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
        raise BackendError(f"{type(self).__name__} 未实现文生视频")

    async def aclose(self) -> None:
        """释放后端持有的连接池。任务结束/测试结束后必须调用。"""
        closer = getattr(self.http, "aclose", None)
        if callable(closer):
            await closer()

    async def test(self) -> dict[str, Any]:
        """连通性自检，尽量不消耗额度：默认只校验配置完整性。"""
        problems: list[str] = []
        if not str(self.provider.get("base_url") or "").strip():
            problems.append("base_url 为空")
        if not (self.provider.get("api_keys") or []):
            problems.append("未配置 API Key")
        if not self.model_id:
            problems.append("model_id 为空")
        return {
            "ok": not problems,
            "protocol": self.protocol,
            "model": self.model_label,
            "problems": problems,
        }

    # ---------- 模型探测 ----------

    # 该协议是否有「列出可用模型」的接口
    models_supported = False
    # 探测结果的补充说明（例如「这是平台全量清单，不代表已开通」）
    models_note = ""

    def models_url(self) -> str:
        """模型列表地址。返回空串表示该协议没有这个接口。"""
        return ""

    def models_urls(self) -> list[str]:
        """候选地址，按顺序尝试，第一个成功的就用它。"""
        url = self.models_url()
        return [url] if url else []

    def models_headers(self) -> dict[str, str]:
        return self.auth_headers()

    def models_params(self) -> dict[str, Any]:
        return {}

    async def list_models(self) -> dict[str, Any]:
        """探测上游可用模型。

        只做只读 GET，不消耗生成额度；超时压到 20 秒，避免拖住界面。
        """
        meta: dict[str, Any] = {
            "protocol": self.protocol,
            "note": self.models_note,
            "provider": self.provider_name,
        }
        urls = self.models_urls()
        if not urls:
            return {
                "ok": False,
                "models": [],
                "message": "该协议没有模型列表接口，请手动填写 model_id",
                **meta,
            }
        http = HTTP(
            timeout=20,
            proxy=self.provider.get("proxy"),
            retries=0,
            headers=self.provider.get("headers") or {},
        )
        errors: list[str] = []
        for url in urls:
            try:
                resp = await http.get(
                    url,
                    headers=self.models_headers(),
                    params=self.models_params() or None,
                    expect=(200,),
                )
                payload = resp.json()
            except BackendError as exc:
                errors.append(str(exc)[:200])
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}"[:200])
                continue

            ids = extract_model_ids(payload)
            if not ids:
                errors.append(f"{url} 有响应但没解析出模型名")
                continue
            return {
                "ok": True,
                "models": [{"id": mid, "kind": guess_kind(mid)} for mid in ids],
                "total": len(ids),
                **meta,
            }
        return {
            "ok": False,
            "models": [],
            "message": "；".join(errors)[:400] or "探测失败",
            **meta,
        }

    # ---------- 结果封装 ----------

    @staticmethod
    def image_item(
        *,
        data: bytes | None = None,
        url: str = "",
        mime: str = "",
        width: int = 0,
        height: int = 0,
        seed: Any = None,
    ) -> dict[str, Any]:
        return {
            "kind": "image",
            "data": data,
            "url": url,
            "mime": mime,
            "width": width,
            "height": height,
            "seed": seed,
        }

    @staticmethod
    def video_item(
        *,
        data: bytes | None = None,
        url: str = "",
        mime: str = "",
        duration: int = 0,
    ) -> dict[str, Any]:
        return {
            "kind": "video",
            "data": data,
            "url": url,
            "mime": mime,
            "duration": duration,
        }

    @staticmethod
    def result(items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        payload = {"items": items}
        payload.update(extra)
        return payload

    # ---------- 参数 ----------

    def model_params(self) -> dict[str, Any]:
        params = dict(self.model.get("params") or {})
        merged = dict(self.provider.get("extra_body") or {})
        merged.update(params)
        return merged

    def merge_params(self, extra: dict[str, Any] | None) -> dict[str, Any]:
        merged = self.model_params()
        merged.update(extra or {})
        return merged

    # ---------- 参数白名单 ----------

    @classmethod
    def allowed_params(cls) -> set[str]:
        """协议认识的可调参数名。

        以 param_schema 为来源，再并上后端内部读取的隐式字段。
        UI / 预设 / 命令传进来的参数只有在这个集合里才发往上游，
        否则像 negative_prompt 这类字段会让严格校验的接口直接 400。
        """
        return set(cls.param_schema) | set(cls.extra_param_names)

    def filter_params(
        self, extra: dict[str, Any] | None
    ) -> tuple[dict[str, Any], list[str]]:
        """按白名单裁剪请求侧参数，返回 (保留的参数, 被丢弃的字段名)。

        只裁剪外部输入。model.params 与 provider.extra_body 是用户显式配置的，
        原样保留（中转站可能需要协议不认识的字段）。
        """
        source = dict(extra or {})
        if self.accept_all_params:
            return source, []
        allowed = self.allowed_params()
        kept: dict[str, Any] = {}
        dropped: list[str] = []
        for key, value in source.items():
            if value is None or value == "":
                continue
            if key in allowed:
                kept[key] = value
            else:
                dropped.append(str(key))
        return kept, dropped


# ---------- 模型列表解析 ----------

_IMAGE_HINT = re.compile(
    r"image|imagen|dall-?e|flux|sdxl|sd-?[0-9]|stable-?diffusion|seedream|seededit"
    r"|wanx|qwen-image|kolors|hunyuan-?image|nano-?banana|ideogram|recraft|pixart"
    r"|midjourney|gpt-image|grok-?image|z-image|jimeng|doubao-?seedream",
    re.IGNORECASE,
)
_VIDEO_HINT = re.compile(
    r"video|seedance|sora|veo[0-9]|kling|hailuo|runway|pika|wan[0-9]|luma|minimax-?hailuo",
    re.IGNORECASE,
)


def guess_kind(model_id: str) -> str:
    """粗判模型是出图还是出视频，供界面默认筛选。判不出返回空串。"""
    text = str(model_id or "")
    if _VIDEO_HINT.search(text):
        return "video"
    if _IMAGE_HINT.search(text):
        return "image"
    return ""


def extract_model_ids(payload: Any, _depth: int = 0) -> list[str]:
    """从各家五花八门的模型列表响应里抽出模型名。

    兼容：
        OpenAI / 方舟   {"data": [{"id": ...}]}
        Gemini          {"models": [{"name": "models/gemini-..."}]}
        Replicate       {"results": [{"owner": ..., "name": ...}]}
        裸数组          ["model-a", "model-b"]
    """
    if _depth > 3 or payload is None:
        return []
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("data", "models", "results", "items", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        else:
            for key in ("result", "response", "body"):
                nested = payload.get(key)
                if isinstance(nested, (dict, list)):
                    return extract_model_ids(nested, _depth + 1)
            return []

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = ""
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            # Replicate 把组织与模型名拆成两个字段，必须拼回 owner/name 才能调用
            owner = str(item.get("owner") or "").strip()
            raw_name = str(item.get("name") or "").strip()
            if owner and raw_name and "/" not in raw_name:
                name = f"{owner}/{raw_name}"
            else:
                name = str(
                    item.get("id")
                    or item.get("model")
                    or item.get("slug")
                    or raw_name
                    or ""
                ).strip()
        if name.startswith("models/"):
            name = name[len("models/") :]
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out
