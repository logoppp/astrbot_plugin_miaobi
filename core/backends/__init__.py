"""妙笔 · 协议注册表。

新增协议只需在这里登记一行，WebUI 的协议下拉和参数面板会自动出现。
"""

from __future__ import annotations

from typing import Any

from ..http import BackendError
from ._help import param_help
from .ark_image import ArkImageBackend
from .ark_video import ArkVideoBackend
from .base import CAP_I2I, CAP_T2I, CAP_T2V, Backend
from .comfyui_roundabout import RoundaboutBackend
from .custom import CustomBackend
from .dashscope_native import DashScopeNativeBackend
from .gemini_image import GeminiImageBackend
from .kling import KlingBackend
from .minimax_video import MiniMaxVideoBackend
from .modelscope import ModelScopeBackend
from .novelai import NovelAIBackend
from .openai_chat_image import OpenAIChatImageBackend
from .openai_images import OpenAIImagesBackend
from .replicate import ReplicateBackend
from .stability import StabilityBackend

# 展示顺序即注册顺序：常用的线上协议在前，本地/自建在后
PROTOCOLS: dict[str, type[Backend]] = {
    "openai_images": OpenAIImagesBackend,
    "ark_image": ArkImageBackend,
    "dashscope_native": DashScopeNativeBackend,
    "modelscope": ModelScopeBackend,
    "gemini_image": GeminiImageBackend,
    "openai_chat_image": OpenAIChatImageBackend,
    "stability": StabilityBackend,
    "replicate": ReplicateBackend,
    "kling": KlingBackend,
    "ark_video": ArkVideoBackend,
    "minimax_video": MiniMaxVideoBackend,
    "novelai": NovelAIBackend,
    "comfyui_roundabout": RoundaboutBackend,
    "custom": CustomBackend,
}


def _schema_with_help(name: str, cls: type[Backend]) -> dict[str, Any]:
    """给每个参数字段补上 help 文案，前端据此显示悬浮说明。"""
    schema: dict[str, Any] = {}
    for key, spec in (cls.param_schema or {}).items():
        item = dict(spec) if isinstance(spec, dict) else {"type": "string"}
        if not item.get("help"):
            text = param_help(name, key)
            if text:
                item["help"] = text
        schema[key] = item
    return schema


def protocol_meta() -> list[dict[str, Any]]:
    out = []
    for name, cls in PROTOCOLS.items():
        out.append(
            {
                "id": name,
                "label": cls.label or name,
                "capabilities": list(cls.capabilities),
                "param_schema": _schema_with_help(name, cls),
                "supports_edit_mode": cls.supports_edit_mode,
                "protocol_limits": cls.protocol_limits,
                "models_supported": bool(getattr(cls, "models_supported", False)),
                "models_note": str(getattr(cls, "models_note", "") or ""),
            }
        )
    return out


def backend_class(protocol: str) -> type[Backend]:
    cls = PROTOCOLS.get(str(protocol or "").strip())
    if cls is None:
        raise BackendError(f"未知协议: {protocol}")
    return cls


__all__ = [
    "Backend",
    "CAP_T2I",
    "CAP_I2I",
    "CAP_T2V",
    "PROTOCOLS",
    "backend_class",
    "protocol_meta",
]
