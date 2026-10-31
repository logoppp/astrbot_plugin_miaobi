"""妙笔 · 提示词优化档案（按模型家族内置）。

每个生图模型吃的提示词风格不一样：NovelAI/Anima 吃 Danbooru tag，
Qwen-Image 吃结构化中文长句，Krea/FLUX 吃英文摄影描述。
这里按「模型名关键词 → 协议 → 能力」匹配家族，给出对应的 system prompt。

用户在配置页改过的文案会覆盖这里的默认值（settings.optimizer.profiles 里 family 相同的那条）。
"""

from __future__ import annotations

from typing import Any

GENERIC = (
    "你是图像生成提示词专家。把用户的一句话扩写成一段可直接送进生图模型的提示词："
    "补充主体、外观、服装、动作、环境、光线、镜头与画质词。"
    "只输出改写后的提示词本身，不要解释、不要引号、不要 markdown。"
)

# NovelAI：{} [] 与 1.2::tag:: 权重、artist: 前缀、官方质量词体系
NAI = (
    "你是 NovelAI（V4 / V4.5）的提示词工程师。把用户的描述改写成 NAI 能吃的英文 tag 列表。\n"
    "规则：\n"
    "1. 全部英文小写，用英文逗号加空格分隔，不要句子、不要解释、不要引号、不要编号。\n"
    "2. 人物数量标签放最前：1girl、1boy、2girls、multiple girls，并补 solo / 1other 这类关系标签。\n"
    "3. 顺序：人物数量与性别 → 角色特征（发型、瞳色、服装、表情）→ 动作与姿势 "
    "→ 场景与背景 → 构图与镜头 → 光线与氛围。\n"
    "4. 权重语法只能写 NAI 的：{} 增强、[] 减弱、1.2::tag:: 精确权重。"
    "严禁使用 SD 的 (tag:1.2) 括号写法，NAI 不认。\n"
    "5. 画师标签写成 artist:画师名，例如 artist:wlop、artist:guweiz；不要裸写画师名。\n"
    "6. 不要写质量词（masterpiece / best quality / very aesthetic 之类），也不要写负面词，"
    "这些由系统按 NAI 官方规则统一追加。\n"
    "7. 只写画面里实际存在的内容，不要臆造用户没提到的元素。\n"
    "8. 最多 40 个 tag。\n"
    "只输出 tag 列表本身。"
)

# Anima：Danbooru tag 体系 + SD 括号权重，画师名直接写
ANIMA = (
    "你是 Anima（Danbooru tag 系二次元模型）的提示词工程师。把用户描述改写成英文 tag 列表。\n"
    "规则：\n"
    "1. 全部英文小写，英文逗号加空格分隔，不要句子、不要解释、不要引号、不要编号。\n"
    "2. 人物数量标签放最前：1girl、1boy、2girls、multiple girls、solo。\n"
    "3. 顺序：人物数量与性别 → 角色特征（发型、瞳色、服装、表情）→ 动作与姿势 "
    "→ 场景与背景 → 构图与镜头 → 光线与氛围。\n"
    "4. 权重语法用 SD 括号写法 (tag:1.2)；严禁使用 NAI 的 {} [] 或 1.2::tag::，Anima 不认。\n"
    "5. 画师标签直接写 Danbooru 画师名，例如 wlop、guweiz、ciloranko；不要加 artist: 前缀。\n"
    "6. 不要写质量词与负面词，由系统按 Anima 预设统一追加。\n"
    "7. 只写画面里实际存在的内容，不要臆造用户没提到的元素。\n"
    "8. 最多 40 个 tag。\n"
    "只输出 tag 列表本身。"
)

# 兼容旧引用（早期版本把所有 tag 模型归在一个家族）
TAG = NAI


QWEN = (
    "你是通义千问 Qwen-Image 的提示词工程师。它吃自然语言，不靠 tag 堆砌。\n"
    "按「主体 → 主体细节 → 动作姿态 → 环境背景 → 构图镜头 → 光线 → 风格画质」的顺序，"
    "写成一段连贯的中文描述（专有名词可保留英文），100~220 字。\n"
    "要求：\n"
    "1. 只写画面里确实存在的内容，不要臆造用户没提的人物或元素。\n"
    "2. 不要写 masterpiece、best quality 这类质量词，也不要用 {} [] 权重语法。\n"
    "3. 画面里有文字时，用中文引号写明文字内容与位置，例如：左上角写着「夏日祭」。\n"
    "4. 改图需求先写「保持……不变」，再写「把……改为……」。\n"
    "只输出提示词本身，不要解释、不要标题、不要 markdown。"
)

ZIMAGE = (
    "你是 Z-Image 的提示词工程师。这个模型对简洁具体的中文描述响应最好。\n"
    "用一段连贯的中文写清：主体是什么、长什么样、在做什么、处在什么环境、什么光线、什么风格与视角。\n"
    "要求：60~120 字；避免同义词堆砌和空泛形容词；不要质量词、不要权重语法；"
    "不要臆造用户没提到的元素。只输出提示词本身。"
)

KREA = (
    "You are a prompt engineer for Krea / FLUX style models. They take English natural language, not tags.\n"
    "Write 2-3 sentences covering: subject and appearance, action, setting, lighting "
    "(e.g. soft window light, golden hour rim light), lens (e.g. 35mm, shallow depth of field), "
    "and texture/style.\n"
    "Rules: no negative prompt, no weight syntax, no quality-word spam, "
    "and do not invent elements the user did not mention.\n"
    "Output only the prompt."
)

MOSS = (
    "你是 MOSS-Image 的提示词工程师。它擅长中文长描述，也擅长画面内文字渲染。\n"
    "按「主体 → 细节 → 动作 → 场景 → 光线 → 镜头 → 风格」写一段自然中文，120~200 字。\n"
    "要求：画面里有文字时写明文字内容、字体风格与摆放位置；不要 tag 堆砌、不要质量词、"
    "不要权重语法；只写画面里实际存在的内容。只输出提示词本身。"
)

SEEDREAM = (
    "你是火山方舟 Seedream（豆包）的提示词工程师。\n"
    "用一段连贯的自然语言写清「主体 + 行为 + 环境」，再补风格、光线、镜头，中文优先。\n"
    "要求：\n"
    "1. 画面内文字必须用双引号包起来并说明位置，例如：招牌上写着\"夏日祭\"。\n"
    "2. 不要质量词、不要权重语法，用正向表达代替否定（写「空无一人的街道」，不要写「不要有人」）。\n"
    "3. 改图需求先写「保留……不变」，再写「把……改为……」。\n"
    "只输出提示词本身。"
)

VIDEO = (
    "你是视频生成提示词工程师。把描述扩写成一条可直接送进视频模型的提示词。\n"
    "按「主体 + 动作过程 + 镜头运动 + 环境光线 + 节奏氛围」组织，1~3 句中文。\n"
    "要求：写清动作的连续性，以及镜头运动（推、拉、摇、移、跟拍）；"
    "不要静态画面的元素堆砌，不要质量词与权重语法。只输出提示词本身。"
)

# 顺序即匹配优先级；generic 兜底，必须放最后
FAMILIES: list[dict[str, Any]] = [
    # anima 必须排在 nai 前面：Anima 也常跑在 novelai 兼容协议上，
    # 否则会被 nai 的 protocols 规则抢先匹配到。
    {
        "key": "anima",
        "label": "Anima（Danbooru tag / SD 权重）",
        "style": "tag",
        "keywords": ("anima",),
        "protocols": (),
        "system_prompt": ANIMA,
        "note": "画师名直接写、(tag:1.2) 权重；与 NovelAI 语法不通，别混用。",
    },
    {
        "key": "nai",
        "label": "NovelAI（Danbooru tag / NAI 权重）",
        "style": "tag",
        "keywords": ("nai-diffusion", "novelai"),
        "protocols": ("novelai",),
        "system_prompt": NAI,
        "note": "{} [] 与 artist: 前缀；质量词、负面词由 NAI 预设追加。",
    },
    {
        "key": "qwen-image",
        "label": "Qwen-Image 系列",
        "style": "text",
        "keywords": ("qwen-image", "qwen_image", "qwen21", "qwen-edit", "qwenimage"),
        "protocols": (),
        "system_prompt": QWEN,
        "note": "自然语言长句，擅长中文字体与图像编辑。",
    },
    {
        "key": "z-image",
        "label": "Z-Image",
        "style": "text",
        "keywords": ("z-image", "zimage", "z_image"),
        "protocols": (),
        "system_prompt": ZIMAGE,
        "note": "偏好简洁具体的中文描述，别堆同义词。",
    },
    {
        "key": "krea2",
        "label": "Krea / FLUX（krea2）",
        "style": "text",
        "keywords": ("krea", "flux"),
        "protocols": (),
        "system_prompt": KREA,
        "note": "英文摄影向描述，不支持负面提示词与权重语法。",
    },
    {
        "key": "moss",
        "label": "MOSS-Image",
        "style": "text",
        "keywords": ("moss",),
        "protocols": (),
        "system_prompt": MOSS,
        "note": "中文长描述，画面内文字渲染是强项。",
    },
    {
        "key": "seedream",
        "label": "Seedream / SeedEdit",
        "style": "text",
        "keywords": ("seedream", "seededit"),
        "protocols": (),
        "system_prompt": SEEDREAM,
        "note": "主体+行为+环境；画面内文字要用双引号。",
    },
    {
        "key": "video",
        "label": "视频模型（Seedance / H3 等）",
        "style": "text",
        "keywords": ("seedance", "hailuo", "kling", "veo", "sora"),
        "protocols": ("ark_video", "minimax_video"),
        "system_prompt": VIDEO,
        "note": "强调动作连续性与镜头运动。",
    },
    {
        "key": "generic",
        "label": "通用（其他模型）",
        "style": "text",
        "keywords": (),
        "protocols": (),
        "system_prompt": GENERIC,
        "note": "没有专门档案时的兜底规则。",
    },
]

_BY_KEY = {item["key"]: item for item in FAMILIES}


def match_family(
    model_id: str, protocol: str = "", capabilities: list[str] | None = None
) -> dict[str, Any]:
    """按模型名关键词 → 协议 → 能力匹配家族，永远返回一个档案（兜底 generic）。"""
    name = str(model_id or "").lower()
    proto = str(protocol or "").strip()
    caps = {str(c) for c in (capabilities or [])}
    # 纯视频模型先判，避免 "doubao-seedance" 被 Seedream 的关键词抢走
    if caps and "t2v" in caps and "t2i" not in caps and "i2i" not in caps:
        return _BY_KEY["video"]
    for item in FAMILIES:
        if item["key"] == "generic":
            continue
        if any(k and k in name for k in item.get("keywords") or ()):
            return item
        if proto and proto in (item.get("protocols") or ()):
            return item
    return _BY_KEY["generic"]


def family_list() -> list[dict[str, Any]]:
    """给配置页用的家族清单（不含用户覆盖）。"""
    return [
        {
            "key": item["key"],
            "label": item["label"],
            "style": item["style"],
            "note": item["note"],
            "system_prompt": item["system_prompt"],
        }
        for item in FAMILIES
    ]


def get_family(key: str) -> dict[str, Any] | None:
    """按 key 取家族定义，供 catalog 显式指定 family 时使用。

    旧配置里的合并家族 `tag` 一律按 NovelAI 处理，避免历史档案失效。
    """
    name = str(key or "").strip()
    if name == "tag":
        name = "nai"
    return _BY_KEY.get(name)
