"""妙笔 · 内置模型清单（逻辑模型 + 常见供应商实现）。

这里只描述「有哪些模型、分别能在哪些协议上跑到」，不包含任何密钥与地址；
真正能否使用取决于用户是否配了对应的供应商。导入时按 protocol + model_id
匹配已有供应商，匹配不到的绑定不会进 chain。
"""

from __future__ import annotations

import re
from typing import Any

CAP_T2I = "t2i"
CAP_I2I = "i2i"
CAP_T2V = "t2v"


def _norm(text: str) -> str:
    return re.sub(r"[\s\-_/.:]+", "", str(text or "").strip().lower())


# bindings 的顺序即建议优先级：本地/官方优先，聚合平台兜底
PRESETS: list[dict[str, Any]] = [
    {
        "id": "gpt-image-1",
        "name": "GPT Image 1",
        "family": "generic",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "OpenAI 官方，指令跟随强，改图稳；中文文字一般。",
        "bindings": [{"protocol": "openai_images", "model_id": "gpt-image-1"}],
    },
    {
        "id": "gemini-2-5-flash-image",
        "name": "Gemini 2.5 Flash Image",
        "family": "generic",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "俗称 nano banana，多轮改图与角色一致性最强的一档。",
        "bindings": [
            {"protocol": "gemini_image", "model_id": "gemini-2.5-flash-image"}
        ],
    },
    {
        "id": "imagen-4",
        "name": "Imagen 4",
        "family": "generic",
        "capabilities": [CAP_T2I],
        "note": "Google 出图，写实质感与光影强，不支持改图。",
        "bindings": [
            {"protocol": "gemini_image", "model_id": "imagen-4.0-generate-001"}
        ],
    },
    {
        "id": "seedream-4-5",
        "name": "Seedream 4.5",
        "family": "seedream",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "火山方舟豆包，中文场景与画面内文字强。",
        "bindings": [
            {"protocol": "ark_image", "model_id": "doubao-seedream-4-5-251128"}
        ],
    },
    {
        "id": "seedream-5-0",
        "name": "Seedream 5.0",
        "family": "seedream",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "方舟最新一代，组图与长文本渲染更好。",
        "bindings": [
            {"protocol": "ark_image", "model_id": "doubao-seedream-5-0-260128"}
        ],
    },
    {
        "id": "qwen-image-3-0",
        "name": "Qwen-Image 3.0",
        "family": "qwen-image",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "通义千问新一代，中文长句与字体渲染强。",
        "bindings": [{"protocol": "openai_images", "model_id": "qwen-image-3.0"}],
    },
    {
        "id": "qwen-image-2-1-pro",
        "name": "Qwen-Image 2.1 Pro",
        "family": "qwen-image",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "2.x 的 Pro 档，稳定、便宜。",
        "bindings": [
            {"protocol": "openai_images", "model_id": "qwen-image-2.1-pro"}
        ],
    },
    {
        "id": "qwen-image-2-0",
        "name": "Qwen-Image 2.0",
        "family": "qwen-image",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "千问原生接口，改图默认用它。",
        "bindings": [
            {"protocol": "dashscope_native", "model_id": "qwen-image-2.0"}
        ],
    },
    {
        "id": "qwen-image",
        "name": "Qwen-Image（开源）",
        "family": "qwen-image",
        "capabilities": [CAP_T2I],
        "note": "开源权重，魔搭可直连，也能在 ComfyUI 自建。",
        "bindings": [{"protocol": "modelscope", "model_id": "Qwen/Qwen-Image"}],
    },
    {
        "id": "qwen-image-edit",
        "name": "Qwen-Image-Edit（开源）",
        "family": "qwen-image",
        "capabilities": [CAP_I2I],
        "note": "开源改图模型，保持主体只改局部。",
        "bindings": [
            {"protocol": "modelscope", "model_id": "Qwen/Qwen-Image-Edit"}
        ],
    },
    {
        "id": "wanx-2-5",
        "name": "通义万相 2.5",
        "family": "qwen-image",
        "capabilities": [CAP_T2I],
        "note": "阿里万相，中文审美向，出图偏商业海报感。",
        "bindings": [
            {"protocol": "dashscope_native", "model_id": "wanx2.5-t2i"}
        ],
    },
    {
        "id": "hunyuan-image-3-0",
        "name": "混元图像 3.0",
        "family": "qwen-image",
        "capabilities": [CAP_T2I],
        "note": "腾讯混元，中文语义与国风题材好。",
        "bindings": [
            {"protocol": "openai_images", "model_id": "hunyuan-image-3.0"}
        ],
    },
    {
        "id": "kling-image",
        "name": "可灵图片",
        "family": "generic",
        "capabilities": [CAP_T2I],
        "note": "快手可灵，人物与质感好，走 AK/SK 签名。",
        "bindings": [{"protocol": "kling", "model_id": "kling-v2"}],
    },
    {
        "id": "flux-1-1-pro",
        "name": "FLUX 1.1 Pro",
        "family": "krea2",
        "capabilities": [CAP_T2I],
        "note": "BFL 商用版，摄影质感强，不吃负面词。",
        "bindings": [
            {"protocol": "replicate", "model_id": "black-forest-labs/flux-1.1-pro"}
        ],
    },
    {
        "id": "flux-1-dev",
        "name": "FLUX.1 Dev",
        "family": "krea2",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "开源主力，魔搭与 Replicate 都能跑。",
        "bindings": [
            {"protocol": "modelscope", "model_id": "black-forest-labs/FLUX.1-dev"},
            {"protocol": "replicate", "model_id": "black-forest-labs/flux-dev"},
        ],
    },
    {
        "id": "flux-1-schnell",
        "name": "FLUX.1 Schnell",
        "family": "krea2",
        "capabilities": [CAP_T2I],
        "note": "4 步出图，便宜快，质量略低于 dev。",
        "bindings": [
            {
                "protocol": "modelscope",
                "model_id": "black-forest-labs/FLUX.1-schnell",
            },
            {"protocol": "replicate", "model_id": "black-forest-labs/flux-schnell"},
        ],
    },
    {
        "id": "sdxl",
        "name": "SDXL",
        "family": "krea2",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "老牌开源底模，生态 LoRA 最多。",
        "bindings": [
            {
                "protocol": "modelscope",
                "model_id": "AI-ModelScope/stable-diffusion-xl-base-1.0",
            },
            {"protocol": "replicate", "model_id": "stability-ai/sdxl"},
        ],
    },
    {
        "id": "sd3-5-large",
        "name": "Stable Diffusion 3.5 Large",
        "family": "krea2",
        "capabilities": [CAP_T2I],
        "note": "Stability 官方 API，构图稳。",
        "bindings": [{"protocol": "stability", "model_id": "sd3.5-large"}],
    },
    {
        "id": "z-image-turbo",
        "name": "Z-Image Turbo",
        "family": "z-image",
        "capabilities": [CAP_T2I],
        "note": "中文短描述友好，9 步出图，魔搭免费额度够用。",
        "bindings": [
            {"protocol": "modelscope", "model_id": "Tongyi-MAI/Z-Image-Turbo"},
            {"protocol": "dashscope_native", "model_id": "z-image-turbo"},
        ],
    },
    {
        "id": "moss-image",
        "name": "MOSS-Image",
        "family": "moss",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "中文长描述与画面内文字渲染是强项，常跑在 ComfyUI 或兼容接口上。",
        "bindings": [
            {"protocol": "comfyui_roundabout", "model_id": "moss-image-t2i"},
            {"protocol": "openai_images", "model_id": "moss-image"},
        ],
    },
    {
        "id": "nai-diffusion-4-5",
        "name": "NovelAI Diffusion V4.5",
        "family": "nai",
        "capabilities": [CAP_T2I],
        "note": "二次元特化，吃 Danbooru tag 与画师串。",
        "bindings": [
            {"protocol": "novelai", "model_id": "nai-diffusion-4-5-full"}
        ],
    },
    {
        "id": "anima-2-9b",
        "name": "Anima 2.9B",
        "family": "anima",
        "capabilities": [CAP_T2I],
        "note": "社区二次元模型，公益站多以 NovelAI 协议兼容。",
        "bindings": [
            {
                "protocol": "novelai",
                "model_id": "Anima-2.9B-int8-turbo-merged",
            },
            {
                "protocol": "comfyui_roundabout",
                "model_id": "Anima-2.9B-int8-turbo-merged",
            },
        ],
    },
    {
        "id": "krea2",
        "name": "Krea 2",
        "family": "krea2",
        "capabilities": [CAP_T2I, CAP_I2I],
        "note": "本地 ComfyUI 两段式工作流，画质取向。",
        "bindings": [
            {"protocol": "comfyui_roundabout", "model_id": "krea2-two-stage"}
        ],
    },
    {
        "id": "seedance-2-5",
        "name": "Seedance 2.5",
        "family": "video",
        "capabilities": [CAP_T2V],
        "note": "火山方舟视频，动作连贯性好。",
        "bindings": [
            {"protocol": "ark_video", "model_id": "doubao-seedance-2-5-260628"}
        ],
    },
    {
        "id": "hailuo-02",
        "name": "海螺 02",
        "family": "video",
        "capabilities": [CAP_T2V],
        "note": "MiniMax 视频，运镜与人物表情好。",
        "bindings": [
            {"protocol": "minimax_video", "model_id": "MiniMax-Hailuo-02"}
        ],
    },
]


def _match(providers: list[dict[str, Any]], spec: dict[str, str]) -> list[dict[str, str]]:
    """在已有供应商里找这个协议 + 模型的实现，按绑定顺序返回。"""
    protocol = str(spec.get("protocol") or "")
    want = _norm(spec.get("model_id") or "")
    out: list[dict[str, str]] = []
    for provider in providers or []:
        if str(provider.get("protocol") or "") != protocol:
            continue
        if not provider.get("enabled", True):
            continue
        for model in provider.get("models") or []:
            if not model.get("enabled", True):
                continue
            model_id = str(model.get("model_id") or model.get("id") or "")
            got = _norm(model_id)
            if not got or not want:
                continue
            if got == want or got.startswith(want) or want.startswith(got):
                out.append(
                    {
                        "provider_id": str(provider.get("id") or ""),
                        "model_id": model_id,
                    }
                )
                break
    return out


def preset_catalog(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """内置清单 + 当前已配供应商的匹配结果，供界面导入。"""
    out: list[dict[str, Any]] = []
    for item in PRESETS:
        chain: list[dict[str, str]] = []
        for spec in item.get("bindings") or []:
            for binding in _match(providers, spec):
                if binding not in chain:
                    chain.append(binding)
        out.append(
            {
                "id": item["id"],
                "name": item["name"],
                "family": item.get("family") or "",
                "capabilities": list(item.get("capabilities") or []),
                "note": item.get("note") or "",
                "chain": chain,
                "optimizer_prompt": "",
                "enabled": True,
                "available": bool(chain),
            }
        )
    return out
