"""NovelAI Images 协议（官方与中转通用）。

响应是 ZIP（内含 image_0.png），V4/V5 必须同时带 v4_prompt 与 v4_negative_prompt，
否则上游返回 500。限流由供应商队列的 cooldown_seconds 负责（中转常见 800s）。
"""

from __future__ import annotations

import io
import random
import zipfile
from typing import Any

from ..http import BackendError
from .base import CAP_T2I, Backend

_GENERATE = "/ai/generate-image"
_STRIP = ("/ai/generate-image", "/ai/generate-image-stream")

DEFAULT_NEGATIVE = (
    "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
    "fewer digits, cropped, worst quality, low quality, normal quality, "
    "jpeg artifacts, signature, watermark, username, blurry"
)

# NovelAI 协议没有模型列表接口，这里给常见型号做内置清单，减少手填出错
KNOWN_MODELS = [
    "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated",
    "nai-diffusion-4-full",
    "nai-diffusion-4-curated-preview",
    "nai-diffusion-3",
    "nai-diffusion-furry-3",
]

# 官方采样器（ddim 仅 v3 支持，放最后）
SAMPLERS = [
    "k_euler_ancestral",
    "k_euler",
    "k_dpmpp_2m",
    "k_dpmpp_2s_ancestral",
    "k_dpmpp_2m_sde",
    "k_dpmpp_sde",
    "k_dpm_2",
    "k_dpm_2_ancestral",
    "ddim",
]

# 噪声调度（相当于调度器）：native 只对 v3 有效，v4 会被上游替换成 karras
NOISE_SCHEDULES = ["karras", "exponential", "polyexponential", "native"]


def normalize_base(url: str) -> str:
    base = str(url or "").strip().split("?")[0].rstrip("/")
    changed = True
    while changed:
        changed = False
        for suffix in _STRIP:
            if base.lower().endswith(suffix):
                base = base[: -len(suffix)].rstrip("/")
                changed = True
                break
    return base or "https://image.novelai.net"


class NovelAIBackend(Backend):
    protocol = "novelai"
    label = "NovelAI"
    capabilities = (CAP_T2I,)
    protocol_limits = {"min_side": 256, "max_side": 2048, "min_pixels": 65536, "align": 64}
    # extra_parameters：中转站透传的原始参数体
    extra_param_names = ("extra_parameters",)
    # NovelAI 没有模型列表接口，用内置清单代替
    models_supported = True
    models_note = "NovelAI 协议没有模型列表接口，这里是内置的常见型号，可直接勾选添加。"

    param_schema = {
        "sampler": {
            "type": "enum",
            "label": "采样器",
            "options": SAMPLERS,
            "default": "k_euler_ancestral",
        },
        "steps": {"type": "int", "label": "步数", "default": 28, "min": 1, "max": 50},
        "scale": {"type": "float", "label": "CFG", "default": 5.0},
        "negative_prompt": {"type": "string", "label": "反向提示词", "default": DEFAULT_NEGATIVE},
        "uc_preset": {
            "type": "enum",
            "label": "UC 预设",
            "options": ["0", "1", "2", "3"],
            "default": "0",
        },
        "quality_toggle": {"type": "bool", "label": "质量标签", "default": True},
        "noise_schedule": {
            "type": "enum",
            "label": "调度器（噪声调度）",
            "options": NOISE_SCHEDULES,
            "default": "karras",
        },
        "cfg_rescale": {"type": "float", "label": "CFG Rescale", "default": 0.0},
        "sm": {"type": "bool", "label": "SMEA", "default": False},
        "sm_dyn": {"type": "bool", "label": "SMEA DYN", "default": False},
        "seed": {"type": "int", "label": "随机种子", "advanced": True},
    }

    async def list_models(self) -> dict[str, Any]:
        return {
            "ok": True,
            "models": [{"id": mid, "kind": "image"} for mid in KNOWN_MODELS],
            "total": len(KNOWN_MODELS),
            "protocol": self.protocol,
            "note": self.models_note,
            "provider": self.provider_name,
        }

    @property
    def base(self) -> str:
        base = normalize_base(self.provider.get("base_url") or "")
        if not base:
            raise BackendError(f"供应商「{self.provider_name}」未配置 base_url")
        return base

    @staticmethod
    def _is_v4(model: str) -> bool:
        lowered = model.lower()
        return "diffusion-4" in lowered or "diffusion-5" in lowered

    def _payload(self, prompt: str, size: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            width, height = (int(x) for x in str(size).lower().split("x", 1))
        except (TypeError, ValueError):
            width, height = 832, 1216
        negative = str(params.get("negative_prompt") or DEFAULT_NEGATIVE).strip()
        seed = int(params.get("seed") or 0)
        if seed <= 0:
            seed = random.randint(1, 2**32 - 1)
        model = self.model_id
        is_v4 = self._is_v4(model)

        parameters: dict[str, Any] = {
            "params_version": 3 if is_v4 else 1,
            "width": width,
            "height": height,
            "scale": float(params.get("scale") or 5.0),
            "sampler": str(params.get("sampler") or "k_euler_ancestral"),
            "steps": max(1, min(int(params.get("steps") or 28), 50)),
            "n_samples": 1,
            "ucPreset": max(0, min(int(params.get("uc_preset") or 0), 3)),
            "qualityToggle": bool(params.get("quality_toggle", True)),
            "dynamic_thresholding": False,
            "controlnet_strength": 1,
            "cfg_rescale": float(params.get("cfg_rescale") or 0.0),
            "noise_schedule": str(params.get("noise_schedule") or "karras"),
            "seed": seed,
            "sm": bool(params.get("sm", False)),
            "sm_dyn": bool(params.get("sm_dyn", False)),
            "add_original_image": False,
            "autoSmea": False,
            "legacy": False,
            "legacy_v3_extend": False,
            "legacy_uc": False,
            "use_coords": False,
            "characterPrompts": [],
            "negative_prompt": negative,
            "reference_image_multiple": [],
            "reference_information_extracted_multiple": [],
            "reference_strength_multiple": [],
            "deliberate_euler_ancestral_bug": False,
            "prefer_brownian": True,
        }
        if is_v4:
            parameters["v4_prompt"] = {
                "caption": {"base_caption": prompt, "char_captions": []},
                "use_coords": False,
                "use_order": True,
            }
            parameters["v4_negative_prompt"] = {
                "caption": {"base_caption": negative, "char_captions": []}
            }
        for key, value in (params.get("extra_parameters") or {}).items():
            if value is not None and key not in ("image", "n"):
                parameters[key] = value
        return {
            "input": prompt,
            "model": model,
            "action": "generate",
            "parameters": parameters,
        }

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
        body = self._payload(prompt, size, merged)
        headers = self.auth_headers()
        headers["Accept"] = "application/zip, */*;q=0.5"
        response = await self.http.post(f"{self.base}{_GENERATE}", json_body=body, headers=headers)
        raw = response.data
        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = [n for n in archive.namelist() if n.lower().endswith(".png")]
                if not names:
                    raise BackendError(f"ZIP 内没有 PNG: {archive.namelist()[:5]}")
                data = archive.read(sorted(names)[0])
        elif raw[:8] == b"\x89PNG\r\n\x1a\n":
            data = raw
        else:
            preview = raw[:200].decode("utf-8", "replace")
            raise BackendError(f"NovelAI 返回不可识别内容: {preview}")
        return self.result(
            [self.image_item(data=data, seed=body["parameters"]["seed"])],
            raw={"model": body["model"], "parameters": body["parameters"]},
        )

    async def test(self) -> dict[str, Any]:
        problems: list[str] = []
        base = ""
        try:
            base = self.base
        except BackendError:
            problems.append("base_url 为空")
        if not (self.provider.get("api_keys") or []):
            problems.append("未配置持久令牌（pst- 开头）")
        if not self.model_id:
            problems.append("model_id 为空")
        return {
            "ok": not problems,
            "protocol": self.protocol,
            "base": base,
            "problems": problems,
            "note": "仅校验配置，未消耗额度",
        }
