"""妙笔 · 线上平台预设。

新建供应商时先选平台，自动填好协议、base_url、常用模型与限制，
省得用户对着各家文档抄 model ID。模型 ID 会随平台更新，
界面里要提示「以控制台为准」。
"""

from __future__ import annotations

from typing import Any

IMAGE_LIMITS = {"min_side": 512, "max_side": 4096, "align": 8}


def _model(
    model_id: str,
    label: str,
    capabilities: list[str],
    *,
    aliases: list[str] | None = None,
    limits: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "display_name": label,
        "capabilities": capabilities,
        "aliases": aliases or [],
        "limits": limits or {},
        "defaults": defaults or {},
        "params": params or {},
    }


PLATFORMS: list[dict[str, Any]] = [
    {
        "id": "ark",
        "label": "火山方舟（豆包）",
        "hint": "Seedream 出图 + Seedance 出视频，国内直连，需在方舟控制台开通模型",
        "providers": [
            {
                "name": "火山方舟·图片",
                "protocol": "ark_image",
                "base_url": "https://ark.cn-beijing.volces.com",
                "concurrency": 2,
                "timeout": 300,
                "cooldown_seconds": 0,
                "models": [
                    _model(
                        "doubao-seedream-4-0-250828",
                        "Seedream 4.0",
                        ["t2i", "i2i"],
                        aliases=["seedream4", "豆包4"],
                        limits={**IMAGE_LIMITS, "max_pixels": 4096 * 4096},
                        defaults={"size": "2048x2048"},
                    ),
                    _model(
                        "doubao-seedream-4-5-251128",
                        "Seedream 4.5",
                        ["t2i", "i2i"],
                        aliases=["seedream45"],
                        limits={**IMAGE_LIMITS, "max_pixels": 4096 * 4096},
                        defaults={"size": "2048x2048"},
                    ),
                    _model(
                        "doubao-seedream-5-0-260128",
                        "Seedream 5.0",
                        ["t2i", "i2i"],
                        aliases=["seedream5"],
                        limits={**IMAGE_LIMITS, "max_pixels": 4096 * 4096},
                        defaults={"size": "2048x2048"},
                    ),
                ],
            },
            {
                "name": "火山方舟·视频",
                "protocol": "ark_video",
                "base_url": "https://ark.cn-beijing.volces.com",
                "concurrency": 1,
                "timeout": 600,
                "poll_timeout": 1800,
                "models": [
                    _model(
                        "doubao-seedance-2-5-260628",
                        "Seedance 2.5",
                        ["t2v"],
                        aliases=["seedance25"],
                        limits={"min_duration": 4, "max_duration": 15},
                        defaults={"duration": 5},
                    ),
                    _model(
                        "doubao-seedance-1-0-pro-250528",
                        "Seedance 1.0 Pro",
                        ["t2v"],
                        aliases=["seedance"],
                        limits={"min_duration": 5, "max_duration": 10},
                        defaults={"duration": 5},
                    ),
                ],
            },
        ],
    },
    {
        "id": "gemini",
        "label": "Google Gemini",
        "hint": "nano banana 系列，出图能带强语义理解；国内需代理",
        "providers": [
            {
                "name": "Gemini",
                "protocol": "gemini_image",
                "base_url": "https://generativelanguage.googleapis.com",
                "concurrency": 2,
                "timeout": 300,
                "models": [
                    _model(
                        "gemini-2.5-flash-image",
                        "Gemini 2.5 Flash Image",
                        ["t2i", "i2i"],
                        aliases=["nano-banana", "香蕉"],
                        limits=IMAGE_LIMITS,
                        defaults={"size": "1024x1024"},
                    ),
                    _model(
                        "gemini-3-pro-image",
                        "Gemini 3 Pro Image",
                        ["t2i", "i2i"],
                        aliases=["nano-banana-pro"],
                        limits=IMAGE_LIMITS,
                        defaults={"size": "1024x1024"},
                    ),
                ],
            }
        ],
    },
    {
        "id": "dashscope",
        "label": "阿里百炼（通义千问）",
        "hint": "qwen-image 系列，图生图与文生图同一接口",
        "providers": [
            {
                "name": "百炼·千问",
                "protocol": "dashscope_native",
                "base_url": "https://dashscope.aliyuncs.com",
                "concurrency": 2,
                "timeout": 300,
                "models": [
                    _model(
                        "qwen-image-3.0",
                        "Qwen Image 3.0",
                        ["t2i", "i2i"],
                        aliases=["qwen3", "千问3"],
                        limits=IMAGE_LIMITS,
                        defaults={"size": "1328x1328"},
                    ),
                    _model(
                        "qwen-image-2.1-pro",
                        "Qwen Image 2.1 Pro",
                        ["t2i", "i2i"],
                        aliases=["qwen21"],
                        limits=IMAGE_LIMITS,
                        defaults={"size": "1328x1328"},
                    ),
                ],
            }
        ],
    },
    {
        "id": "siliconflow",
        "label": "硅基流动 / OpenAI 兼容中转",
        "hint": "任何 OpenAI 兼容的图片接口都能接，把 base_url 换成中转站地址即可",
        "providers": [
            {
                "name": "硅基流动",
                "protocol": "openai_images",
                "base_url": "https://api.siliconflow.cn",
                "concurrency": 2,
                "timeout": 300,
                "models": [
                    _model(
                        "Kwai-Kolors/Kolors",
                        "Kolors",
                        ["t2i"],
                        aliases=["kolors"],
                        limits=IMAGE_LIMITS,
                        defaults={"size": "1024x1024"},
                    )
                ],
            }
        ],
    },
    {
        "id": "openai",
        "label": "OpenAI 官方",
        "hint": "gpt-image 系列，走标准 /images/generations",
        "providers": [
            {
                "name": "OpenAI",
                "protocol": "openai_images",
                "base_url": "https://api.openai.com",
                "concurrency": 2,
                "timeout": 300,
                "models": [
                    _model(
                        "gpt-image-1",
                        "GPT Image 1",
                        ["t2i", "i2i"],
                        aliases=["gptimage"],
                        limits={"min_side": 512, "max_side": 1536, "align": 8},
                        defaults={"size": "1024x1024"},
                    )
                ],
            }
        ],
    },
    {
        "id": "openai_chat",
        "label": "对话式出图（gpt-4o-image 等）",
        "hint": "图片藏在对话回复里，很多中转站用这种形态",
        "providers": [
            {
                "name": "对话式出图",
                "protocol": "openai_chat_image",
                "base_url": "",
                "concurrency": 1,
                "timeout": 300,
                "models": [
                    _model(
                        "gpt-4o-image",
                        "GPT-4o Image",
                        ["t2i", "i2i"],
                        aliases=["4o-image"],
                        limits=IMAGE_LIMITS,
                        defaults={"size": "1024x1024"},
                    )
                ],
            }
        ],
    },
    {
        "id": "minimax",
        "label": "MiniMax 海螺",
        "hint": "视频生成，异步任务，约 1-3 分钟",
        "providers": [
            {
                "name": "海螺视频",
                "protocol": "minimax_video",
                "base_url": "https://api.minimax.chat",
                "concurrency": 1,
                "timeout": 600,
                "poll_timeout": 1800,
                "models": [
                    _model(
                        "MiniMax-Hailuo-2.3",
                        "Hailuo 2.3",
                        ["t2v"],
                        aliases=["hailuo23", "海螺"],
                        limits={"min_duration": 6, "max_duration": 10},
                        defaults={"duration": 6},
                    )
                ],
            }
        ],
    },
    {
        "id": "kling",
        "label": "可灵 Kling",
        "hint": "图片和视频都支持；api_keys 填 AccessKey:SecretKey",
        "providers": [
            {
                "name": "可灵",
                "protocol": "kling",
                "base_url": "https://api.klingai.com",
                "concurrency": 1,
                "timeout": 600,
                "poll_timeout": 1800,
                "models": [
                    _model(
                        "kling-v2-1",
                        "Kling 2.1 图片",
                        ["t2i", "i2i"],
                        aliases=["kling-image"],
                        limits=IMAGE_LIMITS,
                        defaults={"size": "1024x1024"},
                    ),
                    _model(
                        "kling-v2-1-master",
                        "Kling 2.1 Master 视频",
                        ["t2v"],
                        aliases=["kling-video"],
                        limits={"min_duration": 5, "max_duration": 10},
                        defaults={"duration": 5},
                    ),
                ],
            }
        ],
    },
    {
        "id": "stability",
        "label": "Stability AI",
        "hint": "model_id 填 core / ultra / sd3.5-large",
        "providers": [
            {
                "name": "Stability",
                "protocol": "stability",
                "base_url": "https://api.stability.ai",
                "concurrency": 2,
                "timeout": 300,
                "models": [
                    _model(
                        "core",
                        "Stable Image Core",
                        ["t2i", "i2i"],
                        aliases=["sdcore"],
                        limits={"min_side": 256, "max_side": 2048, "align": 8},
                        defaults={"size": "1024x1024"},
                    ),
                    _model(
                        "sd3.5-large",
                        "SD 3.5 Large",
                        ["t2i", "i2i"],
                        aliases=["sd35"],
                        limits={"min_side": 256, "max_side": 2048, "align": 8},
                        defaults={"size": "1024x1024"},
                    ),
                ],
            }
        ],
    },
    {
        "id": "replicate",
        "label": "Replicate",
        "hint": "model_id 填 owner/name，如 black-forest-labs/flux-schnell",
        "providers": [
            {
                "name": "Replicate",
                "protocol": "replicate",
                "base_url": "https://api.replicate.com",
                "concurrency": 2,
                "timeout": 600,
                "poll_timeout": 1200,
                "models": [
                    _model(
                        "black-forest-labs/flux-schnell",
                        "FLUX Schnell",
                        ["t2i"],
                        aliases=["flux"],
                        limits=IMAGE_LIMITS,
                        defaults={"size": "1024x1024"},
                    ),
                    _model(
                        "black-forest-labs/flux-1.1-pro",
                        "FLUX 1.1 Pro",
                        ["t2i", "i2i"],
                        aliases=["flux-pro"],
                        limits=IMAGE_LIMITS,
                        defaults={"size": "1024x1024"},
                    ),
                ],
            }
        ],
    },
    {
        "id": "novelai",
        "label": "NovelAI",
        "hint": "二次元插画；官方直连或中转，注意有冷却",
        "providers": [
            {
                "name": "NovelAI",
                "protocol": "novelai",
                "base_url": "https://image.novelai.net",
                "concurrency": 1,
                "timeout": 300,
                "cooldown_seconds": 0,
                "models": [
                    _model(
                        "nai-diffusion-4-5-full",
                        "NAI 4.5 Full",
                        ["t2i"],
                        aliases=["nai45"],
                        limits={"min_side": 256, "max_side": 2048, "align": 64, "max_pixels": 1048576},
                        defaults={"size": "832x1216"},
                    )
                ],
            }
        ],
    },
    {
        "id": "comfyui",
        "label": "ComfyUI Roundabout（本地/自建）",
        "hint": "自己跑的 ComfyUI，配合 Roundabout 网关",
        "providers": [
            {
                "name": "Roundabout",
                "protocol": "comfyui_roundabout",
                # ComfyUI 默认监听 127.0.0.1:8188；容器部署时改成宿主可达的地址
                "base_url": "http://127.0.0.1:8188",
                "concurrency": 1,
                "timeout": 900,
                "poll_timeout": 1800,
                "models": [
                    _model("krea2", "Krea2", ["t2i", "i2i"], aliases=["krea"]),
                    _model("moss", "MOSS", ["t2i"], aliases=["moss"]),
                ],
            }
        ],
    },
    {
        "id": "custom",
        "label": "自定义接口",
        "hint": "自带请求体模板与结果路径，能接任何 HTTP 出图/出视频服务",
        "providers": [
            {
                "name": "自定义",
                "protocol": "custom",
                "base_url": "",
                "concurrency": 1,
                "timeout": 600,
                "extra_body": {"model": "{model}", "prompt": "{prompt}", "size": "{size}"},
                "models": [
                    _model(
                        "default",
                        "默认模型",
                        ["t2i"],
                        params={"_result_path": "data.*.url", "_kind": "image"},
                    )
                ],
            }
        ],
    },
]


def platform_presets() -> list[dict[str, Any]]:
    """给 UI 用的精简版：去掉内部字段，保留可直接落库的结构。"""
    out: list[dict[str, Any]] = []
    for item in PLATFORMS:
        out.append(
            {
                "id": item["id"],
                "label": item["label"],
                "hint": item.get("hint") or "",
                "providers": [
                    {
                        "name": prov.get("name") or item["label"],
                        "protocol": prov.get("protocol"),
                        "base_url": prov.get("base_url") or "",
                        "concurrency": prov.get("concurrency", 1),
                        "timeout": prov.get("timeout", 300),
                        "poll_timeout": prov.get("poll_timeout", 900),
                        "cooldown_seconds": prov.get("cooldown_seconds", 0),
                        "extra_body": prov.get("extra_body") or {},
                        "models": prov.get("models") or [],
                    }
                    for prov in item["providers"]
                ],
            }
        )
    return out
