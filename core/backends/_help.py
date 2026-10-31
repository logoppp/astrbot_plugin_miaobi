"""妙笔 · 参数说明文案。

单独放一个文件，避免 13 个协议各写一遍同样的解释。
查找顺序：协议专属说明 → 通用说明 → 无（前端不显示问号）。
"""

from __future__ import annotations

# 通用说明：同名参数在各协议含义基本一致
PARAM_HELP: dict[str, str] = {
    "seed": "固定随机种子可以复现同一张图；填 0 或 -1 表示每次随机。",
    "negative_prompt": "不希望画面里出现的内容。仅部分平台支持，不支持的协议会自动忽略。",
    "watermark": "是否在图片角落加平台水印。多数平台默认开启，想要干净画面就关掉。",
    "n": "一次生成几张。数量越多越慢，也可能成倍计费。",
    "steps": "采样步数。越多细节越足也越慢，超过 28 步收益通常很小。",
    "cfg": "提示词引导强度。越高越贴题，过高会僵硬、过饱和。",
    "cfg_scale": "提示词相关度。越高越贴题，但容易出现塑料感或过曝。",
    "guidance_scale": "提示词引导强度。越高越贴题，过低会跑题。",
    "quality": "质量档位（部分平台支持 high / medium / low）。",
    "response_format": "让接口返回图片链接（url）还是 base64（b64_json）。",
    "prompt_extend": "让平台自动扩写提示词。你自己写得很细时建议关掉，避免被改味。",
    "aspect_ratio": "画面比例，如 3:4、16:9；留空则由尺寸计算得出。",
    "resolution": "输出档位。档位越高越清晰，同时更慢、更贵。",
    "output_format": "输出格式。png 无损体积大，jpeg/webp 体积小。",
    "num_outputs": "一次生成几张图片。",
    "disable_safety_checker": "关闭内容安全过滤。部分模型不支持该开关。",
    "strength": "图生图的改动幅度：0 几乎不变，1 完全重画。",
    "style_preset": "风格预设，例如 anime、photographic、cinematic、digital-art。",
    "temperature": "采样随机性。越高越发散、越有意外；图生图建议 0.4~1.0。",
    "max_tokens": "对话式出图时，返回内容的长度上限。",
    "size_hint": "用文字暗示尺寸，如 1024x1024。给不支持 size 字段的中转站兜底。",
    "modalities": "声明返回类型，必须包含 image 才会出图，例如 [\"image\", \"text\"]。",
    "image_size": "输出分辨率档位。4K 更清晰，但更慢也更贵。",
    "safety_off": "关闭安全过滤。部分地区或账号不支持该选项。",
    "prompt_optimizer": "让平台自动优化提示词，适合一句话的粗略描述。",
    "aigc_watermark": "是否叠加 AIGC 隐式水印（合规用途）。",
    "fast_pretreatment": "快速预处理，可缩短排队时间。",
    "first_frame_image": "首帧图片地址（URL 或 data URL），用于图生视频。",
    "camera_fixed": "固定镜头，减少运镜，适合人物特写或产品展示。",
    "generate_audio": "是否同时生成配音。仅部分视频模型支持。",
    "ratio": "画面比例。与分辨率档位共同决定实际像素，不能精确到具体宽高。",
    # ComfyUI Roundabout
    "megapixels": "目标总像素（百万）。1.0 约等于 1024×1024，是最终输出尺寸而不只是输入尺寸。",
    "multiple": "宽高对齐到该数值的整数倍。ComfyUI 一般填 8 或 16。",
    "resolution_selector_node": "工作流里负责分辨率的下拉节点 id。填了之后尺寸会折算成「像素量 + 标准比例」。",
    "resolution_nodes": "精确宽高节点映射，例如 {\"width\": 26, \"height\": 27}。留空则按尺寸直传。",
    "workflow_upscale": "工作流内部放大倍率。目标分辨率会先除以它的平方，再交给工作流。",
    "workflow_overrides": "直接覆写工作流节点字段，例如 {\"115.inputs.width\": 832}。",
    # 自定义模板
    "_method": "自定义接口使用的 HTTP 方法，一般是 POST。",
    "_result_path": "从响应里取媒体地址或 base64 的路径，例如 data.0.url。",
    "_kind": "这个接口返回的是图片还是视频。",
    "_status_path": "异步任务的状态字段路径，例如 data.task_status。",
    "_done_values": "判定任务已完成的取值，多个用逗号分隔，例如 SUCCESS,completed。",
    "_poll_url": "查询任务状态的地址，可用 {task_id} 占位。",
    "_task_path": "从创建响应里取任务 id 的路径，例如 data.task_id。",
    "_headers": "额外请求头，JSON 对象，例如 {\"X-Api-Version\": \"2\"}。",
    # 方舟组图
    "sequential_image_generation": "方舟组图模式：auto 由模型决定是否一次出多张，disabled 只出一张。",
    "max_images": "组图模式下单次最多生成几张（1~15）。",
}

# 协议专属说明：同一参数在不同平台含义/取值不同，覆盖通用文案
PROTOCOL_HELP: dict[tuple[str, str], str] = {
    ("ark_video", "resolution"): "档位 480p / 720p / 1080p，和「画面比例」一起决定实际像素。",
    ("ark_video", "ratio"): "画面比例，配合分辨率档位使用，例如 16:9、9:16。",
    ("kling", "resolution"): "std 标准模式（更快更省）/ pro 高品质模式。",
    ("kling", "mode"): "生成模式：std 标准、pro 高品质。pro 更慢但细节更好。",
    ("kling", "cfg_scale"): "可灵建议 0.3~0.7。越高越贴提示词，过高容易过曝。",
    ("kling", "image_fidelity"): "参考图还原度。越高越接近原图，越低越自由发挥。",
    ("kling", "human_fidelity"): "人物面部还原度。做人物换装、保持长相时调高。",
    ("minimax_video", "resolution"): "档位 720P / 768P / 1080P。越高越清晰，生成越慢。",
    ("dashscope_native", "n"): "千问单次最多 4 张，且 n 必须是整数。",
    ("dashscope_native", "async_mode"): "走异步任务接口：先返回任务 id，再轮询结果。",
    ("gemini_image", "image_size"): "档位 1K / 2K / 4K。4K 更清晰，但更慢也更贵。",
    ("gemini_image", "temperature"): "Gemini 原生没有 CFG，画风差异主要靠这个参数调节。",
    ("novelai", "sampler"): "采样算法。k_euler_ancestral 兼顾质量与速度；k_dpmpp_2m 细节更稳；k_dpmpp_sde 更锐利。",
    ("novelai", "noise_schedule"): "噪声调度，相当于其他平台的「调度器」。karras 最通用；exponential 对比更强；polyexponential 更细腻。v4 模型不支持 native，会被自动换成 karras。",
    ("novelai", "scale"): "NovelAI 的 CFG。常用 4~6，超过 8 容易过饱和、线条发硬。",
    ("novelai", "steps"): "NovelAI 常用 23~28 步。超过 28 收益很小，还更费点数。",
    ("novelai", "uc_preset"): "内置的不良内容抑制档位：0 关闭 / 1 轻 / 2 中 / 3 强。",
    ("novelai", "quality_toggle"): "自动追加 best quality、amazing quality 等质量标签。你已经手写了质量词就关掉。",
    ("novelai", "cfg_rescale"): "CFG 重缩放，用来缓解高 CFG 导致的过饱和。0 关闭，常用 0~0.3。",
    ("novelai", "sm"): "SMEA 采样增强，只对 v3 模型有效，v4 会忽略。",
    ("novelai", "sm_dyn"): "SMEA DYN，与 SMEA 配套使用，只对 v3 模型有效。",
    ("comfyui_roundabout", "megapixels"): "最终输出总像素（百万）。工作流内若有放大节点，输入尺寸会自动按倍率折回去。",
    ("openai_images", "quality"): "部分平台支持 high / medium / low / auto，也有的认 hd。",
    ("openai_images", "prompt_extend"): "千问等平台支持：自动扩写提示词。",
    ("openai_images", "watermark"): "千问等平台支持：是否加平台水印。",
    ("stability", "style_preset"): "官方预设风格，例如 anime、photographic、cinematic、line-art、pixel-art。",
    ("stability", "strength"): "图生图改动幅度：0 保持原图，1 完全重画。0.6~0.8 较常用。",
    ("openai_chat_image", "size_hint"): "写在提示词里暗示尺寸，例如 1024x1024。有些中转站只认这种方法。",
}


def param_help(protocol: str, name: str) -> str:
    return PROTOCOL_HELP.get((protocol, name)) or PARAM_HELP.get(name) or ""
