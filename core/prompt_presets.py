"""妙笔 · 内置提示词预设库。

内容依据各家官方文档整理，不是随手编的：
    Qwen-Image  主体＋环境＋构图＋风格＋光线＋空间关系（官方「万能模版骨架」），
                并要求正向表述、文字用引号标注位置与字体。
    Seedream    官方建议用连贯自然语言写「主体＋行为＋环境」，再补风格/色彩/光影/构图；
                文字要放双引号；编辑类要说清改什么、什么保持不变。
    Gemini      Google Cloud 官方公式 [Subject]+[Action]+[Location]+[Composition]+[Style]，
                强调叙事而非堆关键词；编辑要显式声明保留部分。
    NovelAI     docs.novelai.net 的 Quality / Aesthetic / Year / Dataset 标签体系；
                `background dataset` 与 `fur dataset` 必须放最前，质量与美学标签放最后。

预设三种套用方式见 models.PRESET_MODES。这里只提供默认值，
用户改过之后以配置里的为准（迁移脚本只填空缺）。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- 通用片段

NAI_NEGATIVE = (
    "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
    "fewer digits, cropped, worst quality, low quality, jpeg artifacts, signature, "
    "watermark, username, blurry, artist name"
)

NAI_QUALITY = "masterpiece, best quality, amazing quality, very aesthetic, absurdres"

# Anima 是 Danbooru tag + SD 括号权重的体系，不吃 NAI 的 artist: 前缀与美学标签
ANIMA_NEGATIVE = (
    "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
    "fewer digits, cropped, worst quality, low quality, jpeg artifacts, signature, "
    "watermark, username, blurry"
)

ANIMA_QUALITY = "masterpiece, best quality, amazing quality, absurdres"


def _preset(
    name: str,
    prompt: str,
    mode: str = "append",
    *,
    negative: str = "",
    note: str = "",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "mode": mode,
        "prompt": prompt,
        "negative_prompt": negative,
        "note": note,
        "params": params or {},
    }


# ---------------------------------------------------------------- Z-Image

ZIMAGE = [
    _preset(
        "写实摄影",
        "自然光摄影，主体清晰锐利，浅景深，柔和阴影，真实材质质感",
        note="补在提示词末尾，把描述拉向纪实摄影",
    ),
    _preset(
        "电影感",
        "电影级构图，柔和逆光勾勒轮廓，轻微胶片颗粒，暖色调，氛围感强",
    ),
    _preset(
        "中文海报",
        "{prompt}，居中排版，主标题用粗黑体居中偏上，副标题在其下方，"
        "整体留白充足，高对比度，印刷质感，文字清晰无变形",
        mode="wrap",
        note="结构模板：把主体描述填进 {prompt}",
    ),
    _preset(
        "产品棚拍",
        "{prompt}，置于浅灰无缝背景，柔和定向棚光，材质与边缘细节清晰，右侧留白用于文案",
        mode="wrap",
    ),
]

# ---------------------------------------------------------------- Qwen-Image

QWEN = [
    _preset(
        "写实人像",
        "{prompt}，半身特写，商业人像摄影，柔和自然侧光，浅景深，"
        "皮肤质感真实，发丝细节清晰，背景简洁不抢主体",
        mode="wrap",
        negative="低分辨率，模糊，变形的手，多余手指，过度磨皮，蜡感皮肤，五官扭曲",
        note="官方进阶公式：主体＋场景＋风格＋镜头＋氛围＋细节修饰",
    ),
    _preset(
        "国风插画",
        "{prompt}，国风水墨插画，工笔细节，宣纸质感，留白构图，淡青与朱红点缀，意境悠远",
        mode="wrap",
    ),
    _preset(
        "海报排版",
        "{prompt}，现代海报设计，主标题居中偏上，副标题在标题下方，"
        "画面简洁，高对比排版，字体清晰锐利，无错字",
        mode="wrap",
        note="官方要求：文字用引号标注，并写清位置与字体",
    ),
    _preset(
        "产品广告",
        "{prompt}，产品置于浅色混凝土台面，三点柔光棚拍，四分之三视角，"
        "右侧留白用于文案，高端广告质感",
        mode="wrap",
    ),
    _preset(
        "正向补全",
        "构图简洁，主体突出，色彩协调，光线自然，画面干净",
        note="官方技巧：把「不要xx」改成正向表达，这里只补正向词",
    ),
]

# ---------------------------------------------------------------- Seedream

SEEDREAM = [
    _preset(
        "商业产品图",
        "{prompt}，浅色混凝土台面，柔和定向棚光，四分之三视角，"
        "右侧留白用于产品文案，干净高端的产品广告风格",
        mode="wrap",
        note="官方示例结构：主体＋行为＋环境＋风格＋光影＋构图＋用途",
    ),
    _preset(
        "海报文案",
        "{prompt}，简洁版式，主标题“标题文字”用粗体无衬线字居中，副标题在其下方，"
        "背景干净，高对比，文字清晰可读",
        mode="wrap",
        note="官方要求：要生成的文字必须放双引号里",
    ),
    _preset(
        "绘本插画",
        "{prompt}，儿童绘本插画风格，柔和pastel配色，温暖治愈，手绘质感，构图简洁",
        mode="wrap",
    ),
    _preset(
        "参考图编辑",
        "参考原图，保持人物面部、发型、姿态、构图和光线不变，只按上面的描述替换背景",
        mode="append",
        note="编辑类用法：说清改什么 + 什么必须保持不变（官方推荐写法）",
    ),
]

# ---------------------------------------------------------------- Gemini

GEMINI = [
    _preset(
        "叙事场景",
        "{prompt}，中景构图，居中取景，电影级光影，中画幅胶片质感，"
        "颗粒感鲜明，色彩层次丰富",
        mode="wrap",
        note="官方公式：主体＋动作＋场景＋构图＋风格，用叙事句而非关键词堆叠",
    ),
    _preset(
        "文字海报",
        "{prompt}，主标题“TITLE”使用粗体无衬线居中排版，副标题在其下方，"
        "深色渐变背景，排版干净，字形锐利",
        mode="wrap",
        note="官方建议：文字加引号，并指定字体风格",
    ),
    _preset(
        "参考图改造",
        "参考原图，保持人物特征、姿势、构图与视角完全不变，只按上面的描述调整光线与背景",
        mode="append",
        note="官方：编辑时要显式说明「哪些保持不变」",
    ),
    _preset(
        "超现实",
        "{prompt}，超现实构图，荒诞而自洽的物理关系，柔和散射光，细腻材质，"
        "1990 年代科幻小说封面的印刷颗粒",
        mode="wrap",
    ),
]

# ---------------------------------------------------------------- NovelAI 画师串
# NAI 的画师标签要带 artist: 前缀，权重语法是 {} [] 与 1.2::tag::
NOVELAI = [
    _preset(
        "厚涂光影",
        f"artist:wlop, artist:guweiz, artist:artgerm, {NAI_QUALITY}",
        negative=NAI_NEGATIVE,
        note="NAI 需 artist: 前缀；厚涂质感、强光影对比，适合有体积感的半身像",
    ),
    _preset(
        "日系赛璐璐",
        f"artist:ask_(askzy), artist:mika_pikazo, artist:atdan, {NAI_QUALITY}",
        negative=NAI_NEGATIVE,
        note="干净线稿 + 平涂上色，色彩通透",
    ),
    _preset(
        "韩系厚涂",
        f"artist:ciloranko, artist:iling, artist:miv4t, {NAI_QUALITY}",
        negative=NAI_NEGATIVE,
        note="柔和厚涂、肤色细腻，偏人物立绘",
    ),
    _preset(
        "动感构图",
        f"artist:yoneyama_mai, artist:rella, {NAI_QUALITY}",
        negative=NAI_NEGATIVE,
        note="大透视与动势，适合动作场景",
    ),
    _preset(
        "极简清爽",
        NAI_QUALITY,
        negative=NAI_NEGATIVE,
        note="不加画师，只留官方质量与美学标签，风格最中性",
    ),
    _preset(
        "背景风景",
        "background dataset, {prompt}, scenery, no humans, detailed background, "
        f"{NAI_QUALITY}",
        mode="wrap",
        negative=NAI_NEGATIVE,
        note="background dataset 必须放最前，官方规定",
    ),
]

# ---------------------------------------------------------------- Anima 画师串
# Anima 是 Danbooru tag + SD 括号权重：画师名裸写，没有 artist: 前缀，
# 也没有 NAI 那套 quality / aesthetic / dataset 标签
ANIMA = [
    _preset(
        "厚涂光影",
        f"wlop, guweiz, artgerm, {ANIMA_QUALITY}",
        negative=ANIMA_NEGATIVE,
        note="画师名裸写，别加 artist: 前缀（与 NovelAI 语法不同）",
    ),
    _preset(
        "日系赛璐璐",
        f"ask_(askzy), mika_pikazo, atdan, {ANIMA_QUALITY}",
        negative=ANIMA_NEGATIVE,
        note="干净线稿 + 平涂，Danbooru 画师 tag 直接写",
    ),
    _preset(
        "韩系厚涂",
        f"ciloranko, iling, miv4t, {ANIMA_QUALITY}",
        negative=ANIMA_NEGATIVE,
        note="柔和厚涂、肤色细腻",
    ),
    _preset(
        "动感构图",
        f"yoneyama_mai, rella, {ANIMA_QUALITY}",
        negative=ANIMA_NEGATIVE,
        note="大透视与动势",
    ),
    _preset(
        "极简清爽",
        ANIMA_QUALITY,
        negative=ANIMA_NEGATIVE,
        note="不加画师，只留质量词，风格最中性",
    ),
    _preset(
        "纯用户输入",
        "{prompt}",
        mode="wrap",
        negative=ANIMA_NEGATIVE,
        note="不追加画师与质量词，完全按优化结果来",
    ),
]

# ---------------------------------------------------------------- 视频

VIDEO = [
    _preset(
        "缓慢推进",
        "{prompt}，镜头缓慢推近，浅景深，柔和棚光，画面稳定，背景简洁",
        mode="wrap",
    ),
    _preset(
        "环绕运镜",
        "{prompt}，镜头缓慢环绕主体，低角度仰拍，逆光勾边，电影质感，轻微胶片颗粒",
        mode="wrap",
    ),
    _preset(
        "手持纪实",
        "{prompt}，中景，人物自然动作，镜头轻微手持晃动，自然光，纪实风格",
        mode="wrap",
    ),
]

# ---------------------------------------------------------------- 通用兜底

GENERIC_IMAGE = [
    _preset(
        "写实",
        "photorealistic, natural lighting, shallow depth of field, detailed textures, 85mm lens",
    ),
    _preset(
        "插画",
        "digital illustration, clean lineart, vibrant colors, soft shading, highly detailed",
    ),
    _preset(
        "电影感",
        "cinematic composition, dramatic lighting, film grain, anamorphic, muted color palette",
    ),
]

GENERIC_VIDEO = [
    _preset(
        "稳定运镜",
        "{prompt}, slow dolly in, steady camera, soft studio lighting, shallow depth of field",
        mode="wrap",
    ),
]


# ---------------------------------------------------------------- 匹配

def _match(model_id: str, protocol: str) -> list[dict[str, Any]]:
    mid = str(model_id or "").lower()

    # Anima 常跑在 NovelAI 兼容协议上，必须先按模型名分流，
    # 否则会拿到 NAI 的 artist: 画师串与美学标签
    if "anima" in mid:
        return ANIMA
    if "nai-diffusion" in mid or protocol == "novelai":
        return NOVELAI
    if "seedream" in mid or protocol == "ark_image":
        return SEEDREAM
    if "gemini" in mid or protocol == "gemini_image":
        return GEMINI
    if "z-image" in mid:
        return ZIMAGE
    if "qwen" in mid and "image" in mid:
        return QWEN
    if protocol in {"ark_video", "minimax_video"}:
        return VIDEO
    if protocol in {"openai_images", "stability", "replicate", "kling"}:
        return GENERIC_IMAGE
    if protocol == "comfyui_roundabout":
        return GENERIC_IMAGE
    return GENERIC_IMAGE


def presets_for(
    model_id: str, protocol: str, capabilities: list[str] | None = None
) -> list[dict[str, Any]]:
    """按模型 ID 优先、协议次之，返回可直接落库的预设（未带 id，由 normalize 生成）。

    可灵这类「图片和视频共用一套协议」的平台，靠能力判断走哪套预设。
    """
    caps = set(capabilities or [])
    if caps and "t2v" in caps and "t2i" not in caps:
        return [dict(item) for item in VIDEO]
    return [dict(item) for item in _match(model_id, protocol)]
