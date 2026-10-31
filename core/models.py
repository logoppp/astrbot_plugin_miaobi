"""妙笔 · 数据模型与规范化。

配置全部以普通 dict 存储（JSON 友好），这里只负责补默认值、剔除脏字段、生成 id。
"""

from __future__ import annotations

import re
import secrets
import time
from typing import Any

CAP_T2I = "t2i"
CAP_I2I = "i2i"
CAP_T2V = "t2v"
CAPABILITIES = (CAP_T2I, CAP_I2I, CAP_T2V)
CAPABILITY_LABELS = {
    CAP_T2I: "文生图",
    CAP_I2I: "图生图",
    CAP_T2V: "文生视频",
}

# 全局兜底：任何自定义分辨率都不能越过它
GLOBAL_LIMITS = {
    "min_side": 64,
    "max_side": 8192,
    "min_pixels": 4096,
    "max_pixels": 36_000_000,
}

DEFAULT_PROVIDER_LIMITS = {
    "min_side": 256,
    "max_side": 4096,
    "min_pixels": 0,
    "max_pixels": 0,
    "align": 8,
    "ratios": [],
    "min_duration": 0,
    "max_duration": 0,
}

DEFAULT_PROVIDER: dict[str, Any] = {
    "id": "",
    "name": "",
    "protocol": "openai_images",
    "base_url": "",
    "api_keys": [],
    "headers": {},
    "proxy": "",
    "timeout": 600,
    "max_retries": 1,
    "concurrency": 1,
    "cooldown_seconds": 0,
    "max_queue": 8,
    "poll_timeout": 900,
    "edit_mode": "edits",
    "extra_body": {},
    "enabled": True,
    "models": [],
}

DEFAULT_MODEL: dict[str, Any] = {
    "id": "",
    "provider_id": "",
    "model_id": "",
    "display_name": "",
    "capabilities": [CAP_T2I],
    "aliases": [],
    "defaults": {},
    "limits": {},
    "params": {},
    "prompt_presets": [],
    # 默认套用的预设（画师串）：生成时没显式选预设就用它
    "default_preset_id": "",
    "presets_seeded": False,
    # 内置预设库的版本号：升级到新版本时会重新播种一次（仅 tag 模型）
    "presets_version": 0,
    "enabled": True,
}

# 预设的三种套用方式：
#   append  —— 用户输入 + 预设（画师串、质量标签、风格后缀）
#   wrap    —— 预设里用 {prompt} 占位，把用户输入嵌进固定结构
#   replace —— 直接用预设覆盖（完整示例）
PRESET_MODES = ("append", "wrap", "replace")

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 2,
    "defaults": {
        CAP_T2I: "",
        CAP_I2I: "",
        CAP_T2V: "",
    },
    # 逻辑模型：只描述「同一个模型可以用哪些供应商实现」，参数仍留在 provider / provider.model 上。
    # 数组顺序不参与选择，选中哪个由 defaults 决定；条目内的 chain 顺序即默认→回退。
    "catalog": [],
    "fallback_chain": {
        CAP_T2I: [],
        CAP_I2I: [],
        CAP_T2V: [],
    },
    "optimizer": {
        "enabled": True,
        "on_error": "use_original",
        "cache": True,
        # 优化提示词用的 LLM 供应商，按顺序回退；空=跟随会话默认
        "llm_providers": [],
        # 兼容旧配置：单个供应商标识，读取时并入 llm_providers
        "llm_provider_id": "",
        "profiles": [],
        "map": {},
    },
    "limits": dict(GLOBAL_LIMITS),
    "queue": {
        "notify_on_failure": True,
    },
}

# 逻辑模型条目：把「同一个模型的多个供应商实现」收在一条记录里
DEFAULT_CATALOG_ENTRY: dict[str, Any] = {
    "id": "",
    "name": "",
    # 提示词优化家族（tag / qwen-image / krea2 …），空=按模型名自动匹配
    "family": "",
    "capabilities": [CAP_T2I],
    # 有序绑定：[{"provider_id": "p_x", "model_id": "flux-dev"}]，第一个是默认供应商
    "chain": [],
    # 该模型的专属优化提示词，空=用家族的
    "optimizer_prompt": "",
    "enabled": True,
}



def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, secrets.token_hex(4))


def now_ts() -> int:
    return int(time.time())


def _as_str(value: Any, default: str = "") -> str:
    text = str(value if value is not None else "").strip()
    return text or default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    return [value]


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _str_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        text = _as_str(item)
        if text and text not in out:
            out.append(text)
    return out


def normalize_limits(raw: Any) -> dict[str, Any]:
    src = _as_dict(raw)
    limits = dict(DEFAULT_PROVIDER_LIMITS)
    for key in ("min_side", "max_side", "min_pixels", "max_pixels", "align"):
        if key in src:
            limits[key] = _as_int(src.get(key), limits[key])
    for key in ("min_duration", "max_duration"):
        if key in src:
            limits[key] = _as_int(src.get(key), 0)
    if "ratios" in src:
        limits["ratios"] = _str_list(src.get("ratios"))
    # 自洽性保护：min 不得大于 max
    if limits["align"] < 1:
        limits["align"] = 1
    if limits["max_side"] < limits["min_side"]:
        limits["max_side"] = limits["min_side"]
    if limits["max_pixels"] and limits["max_pixels"] < limits["min_pixels"]:
        limits["max_pixels"] = limits["min_pixels"]
    return limits


def normalize_presets(raw: Any) -> list[dict[str, Any]]:
    """提示词预设：名称 + 模板 + 套用方式，可自带负面词与参数。"""
    out: list[dict[str, Any]] = []
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        # 正文允许为空：用户可能先建一条只填负面词的预设，别在保存时静默丢掉
        text = str(item.get("prompt") or "")
        mode = _as_str(item.get("mode"), "append").lower()
        if mode not in PRESET_MODES:
            mode = "append"
        out.append(
            {
                "id": _as_str(item.get("id")) or new_id("ps"),
                "name": _as_str(item.get("name")) or "未命名预设",
                "mode": mode,
                "prompt": text,
                "negative_prompt": str(item.get("negative_prompt") or ""),
                "note": str(item.get("note") or ""),
                "params": _as_dict(item.get("params")),
            }
        )
    return out


def normalize_model(raw: Any, provider_id: str = "") -> dict[str, Any]:
    src = _as_dict(raw)
    model = dict(DEFAULT_MODEL)
    model["id"] = _as_str(src.get("id")) or new_id("m")
    model["provider_id"] = _as_str(src.get("provider_id")) or provider_id
    model["model_id"] = _as_str(src.get("model_id"))
    model["display_name"] = _as_str(src.get("display_name")) or model["model_id"] or model["id"]
    caps = [c for c in _str_list(src.get("capabilities")) if c in CAPABILITIES]
    model["capabilities"] = caps or [CAP_T2I]
    model["aliases"] = _str_list(src.get("aliases"))
    model["defaults"] = _as_dict(src.get("defaults"))
    model["limits"] = normalize_limits(src.get("limits"))
    model["params"] = _as_dict(src.get("params"))
    model["prompt_presets"] = normalize_presets(src.get("prompt_presets"))
    # 默认预设必须真实存在，否则生成时会静默失效
    default_preset = _as_str(src.get("default_preset_id"))
    if default_preset and not any(
        str(item.get("id")) == default_preset for item in model["prompt_presets"]
    ):
        default_preset = ""
    model["default_preset_id"] = default_preset
    # 记录「内置预设已补过」：用户把预设删空后不该再被自动塞回来
    model["presets_seeded"] = _as_bool(src.get("presets_seeded"), False)
    model["presets_version"] = max(0, _as_int(src.get("presets_version"), 0))
    model["enabled"] = _as_bool(src.get("enabled"), True)
    return model


def normalize_provider(raw: Any) -> dict[str, Any]:
    src = _as_dict(raw)
    provider = dict(DEFAULT_PROVIDER)
    provider["id"] = _as_str(src.get("id")) or new_id("p")
    provider["name"] = _as_str(src.get("name")) or provider["id"]
    provider["protocol"] = _as_str(src.get("protocol"), "openai_images")
    provider["base_url"] = _as_str(src.get("base_url")).rstrip("/")
    provider["api_keys"] = _str_list(src.get("api_keys"))
    provider["headers"] = {str(k): str(v) for k, v in _as_dict(src.get("headers")).items()}
    provider["proxy"] = _as_str(src.get("proxy"))
    provider["timeout"] = max(10, min(_as_int(src.get("timeout"), 600), 3600))
    provider["max_retries"] = max(0, min(_as_int(src.get("max_retries"), 1), 5))
    provider["concurrency"] = max(1, min(_as_int(src.get("concurrency"), 1), 8))
    provider["cooldown_seconds"] = max(0.0, min(_as_float(src.get("cooldown_seconds"), 0.0), 7200.0))
    provider["max_queue"] = max(1, min(_as_int(src.get("max_queue"), 8), 64))
    provider["poll_timeout"] = max(30, min(_as_int(src.get("poll_timeout"), 900), 7200))
    provider["edit_mode"] = _as_str(src.get("edit_mode"), "edits")
    provider["extra_body"] = _as_dict(src.get("extra_body"))
    provider["enabled"] = _as_bool(src.get("enabled"), True)
    provider["models"] = [
        normalize_model(item, provider["id"]) for item in _as_list(src.get("models"))
        if isinstance(item, dict)
    ]
    return provider


def slugify(text: str) -> str:
    """把模型名变成可读 id：black-forest-labs/FLUX.1-dev → black-forest-labs-flux-1-dev。"""
    raw = re.sub(r"[^a-zA-Z0-9]+", "-", str(text or "").strip().lower())
    return raw.strip("-")[:48]


def normalize_catalog_entry(raw: Any) -> dict[str, Any]:
    src = _as_dict(raw)
    entry = dict(DEFAULT_CATALOG_ENTRY)
    entry["id"] = _as_str(src.get("id")) or new_id("c")
    entry["name"] = _as_str(src.get("name")) or entry["id"]
    family = _as_str(src.get("family"))
    if family == "tag":
        # v2 早期把 NAI 与 Anima 合称 tag，按名字拆回各自家族
        probe = f"{entry['name']} {entry['id']}".lower()
        family = "anima" if "anima" in probe else "nai"
    entry["family"] = family
    caps = [c for c in _str_list(src.get("capabilities")) if c in CAPABILITIES]
    entry["capabilities"] = caps or [CAP_T2I]
    entry["optimizer_prompt"] = str(src.get("optimizer_prompt") or "")
    entry["enabled"] = _as_bool(src.get("enabled"), True)

    chain: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in _as_list(src.get("chain")):
        if isinstance(item, str):
            # 允许简写 "provider_id/model_id"
            pid, _, mid = item.partition("/")
            item = {"provider_id": pid, "model_id": mid}
        if not isinstance(item, dict):
            continue
        binding = {
            "provider_id": _as_str(item.get("provider_id")),
            "model_id": _as_str(item.get("model_id")),
        }
        if not binding["provider_id"] and not binding["model_id"]:
            continue
        key = (binding["provider_id"], binding["model_id"])
        if key in seen:
            continue
        seen.add(key)
        chain.append(binding)
    entry["chain"] = chain
    return entry


def normalize_catalog(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        entry = normalize_catalog_entry(item)
        # id 重复会让 defaults 指错人，补后缀而不是丢弃
        base = entry["id"]
        n = 2
        while entry["id"] in seen_ids:
            entry["id"] = f"{base}-{n}"
            n += 1
        seen_ids.add(entry["id"])
        out.append(entry)
    return out


def index_physical_models(providers: Any) -> dict[str, dict[str, Any]]:
    """物理模型 key → {provider_id, model_id, model, provider}。

    旧配置的 defaults / fallback_chain 存的是这个 key，迁移时要靠它还原成绑定。
    """
    index: dict[str, dict[str, Any]] = {}
    for provider in _as_list(providers):
        if not isinstance(provider, dict):
            continue
        pid = _as_str(provider.get("id"))
        for model in _as_list(provider.get("models")):
            if not isinstance(model, dict):
                continue
            key = _as_str(model.get("id"))
            if not key:
                continue
            index[key] = {
                "provider_id": pid,
                "model_id": _as_str(model.get("model_id")) or key,
                "model": model,
                "provider": provider,
            }
    return index


def _catalog_add_binding(
    catalog: list[dict[str, Any]],
    *,
    provider_id: str,
    model_id: str,
    display_name: str,
    capabilities: list[str],
    family: str = "",
) -> str:
    """按 model_id 归并：同一模型的多供应商落进同一条 catalog 的 chain。"""
    want = slugify(model_id) or new_id("c")
    for entry in catalog:
        if entry["id"] == want or slugify(entry["name"]) == want:
            binding = {"provider_id": provider_id, "model_id": model_id}
            if binding not in entry["chain"]:
                entry["chain"].append(binding)
            for cap in capabilities:
                if cap not in entry["capabilities"]:
                    entry["capabilities"].append(cap)
            return entry["id"]
    catalog.append(
        {
            "id": want,
            "name": display_name or model_id,
            "family": family,
            "capabilities": list(capabilities),
            "chain": [{"provider_id": provider_id, "model_id": model_id}],
            "optimizer_prompt": "",
            "enabled": True,
        }
    )
    return want


def migrate_legacy_defaults(
    settings: dict[str, Any],
    providers: Any,
    index: dict[str, dict[str, Any]] | None = None,
) -> None:
    """把 v1 的「defaults 存物理模型 key」迁移成 v2 的「defaults 存 catalog id」。

    已经在 catalog 里的 id 原样保留，所以重复调用是幂等的。
    """
    catalog: list[dict[str, Any]] = settings["catalog"]
    known = {str(item.get("id")) for item in catalog}
    idx = index if index is not None else index_physical_models(providers)
    if not idx:
        return

    def ensure(token: str, capability: str) -> str | None:
        info = idx.get(token)
        if not info:
            return None
        model = info["model"]
        caps = [c for c in (model.get("capabilities") or []) if c in CAPABILITIES] or [capability]
        cid = _catalog_add_binding(
            catalog,
            provider_id=info["provider_id"],
            model_id=info["model_id"],
            display_name=_as_str(model.get("display_name")) or info["model_id"],
            capabilities=caps,
        )
        known.add(cid)
        return cid

    defaults = settings["defaults"]
    for cap in CAPABILITIES:
        token = _as_str(defaults.get(cap))
        if not token or token in known:
            continue
        cid = ensure(token, cap)
        if cid:
            defaults[cap] = cid
        # 找不到映射就原样留着，交给界面提示，别静默清空

    # 旧 fallback_chain 里的物理模型也收进 catalog，供用户重新排序；
    # 不自动串联不同模型（新语义下 chain 是「同一模型换供应商」）。
    legacy_chain = settings.get("fallback_chain") or {}
    for cap in CAPABILITIES:
        for token in legacy_chain.get(cap) or []:
            token = _as_str(token)
            if not token or token in known:
                continue
            ensure(token, cap)


def normalize_settings(raw: Any, providers: Any = None) -> dict[str, Any]:
    src = _as_dict(raw)
    settings = {
        "version": 2,
        "defaults": dict(DEFAULT_SETTINGS["defaults"]),
        "catalog": [],
        "fallback_chain": {k: [] for k in CAPABILITIES},
        "optimizer": {
            "enabled": True,
            "on_error": "use_original",
            "cache": True,
            "llm_providers": [],
            "llm_provider_id": "",
            "profiles": [],
            "map": {},
        },
        "limits": dict(GLOBAL_LIMITS),
        "queue": dict(DEFAULT_SETTINGS["queue"]),
    }

    settings["catalog"] = normalize_catalog(src.get("catalog"))

    defaults = _as_dict(src.get("defaults"))
    for cap in CAPABILITIES:
        settings["defaults"][cap] = _as_str(defaults.get(cap))

    chain = _as_dict(src.get("fallback_chain"))
    for cap in CAPABILITIES:
        settings["fallback_chain"][cap] = _str_list(chain.get(cap))

    optimizer = _as_dict(src.get("optimizer"))
    settings["optimizer"]["enabled"] = _as_bool(optimizer.get("enabled"), True)
    settings["optimizer"]["on_error"] = _as_str(optimizer.get("on_error"), "use_original")
    settings["optimizer"]["cache"] = _as_bool(optimizer.get("cache"), True)
    settings["optimizer"]["llm_provider_id"] = _as_str(optimizer.get("llm_provider_id"))
    # 回退路径：新字段优先，旧单值自动并进来
    llm_chain = _str_list(optimizer.get("llm_providers"))
    legacy_llm = settings["optimizer"]["llm_provider_id"]
    if not llm_chain and legacy_llm:
        llm_chain = [legacy_llm]
    settings["optimizer"]["llm_providers"] = llm_chain
    if not settings["optimizer"]["llm_provider_id"] and llm_chain:
        settings["optimizer"]["llm_provider_id"] = llm_chain[0]
    profiles = []
    for item in _as_list(optimizer.get("profiles")):
        if not isinstance(item, dict):
            continue
        profiles.append(
            {
                "id": _as_str(item.get("id")) or new_id("opt"),
                "name": _as_str(item.get("name")) or "未命名档案",
                # family 是家族覆盖的键（nai / anima / qwen-image / krea2 …）
                # 旧版本把两种 tag 模型合称 tag，统一按 NovelAI 处理
                "family": (
                    "nai"
                    if _as_str(item.get("family")) == "tag"
                    else _as_str(item.get("family"))
                ),
                "style": _as_str(item.get("style")),
                "llm_provider_id": _as_str(item.get("llm_provider_id")),
                "system_prompt": str(item.get("system_prompt") or ""),
                "timeout": max(3, min(_as_int(item.get("timeout"), 25), 180)),
            }
        )
    settings["optimizer"]["profiles"] = profiles
    settings["optimizer"]["map"] = {
        str(k): str(v) for k, v in _as_dict(optimizer.get("map")).items() if str(v).strip()
    }

    limits = normalize_limits(src.get("limits"))
    global_limits = dict(GLOBAL_LIMITS)
    for key in ("min_side", "max_side", "min_pixels", "max_pixels"):
        value = _as_int(limits.get(key), global_limits[key])
        if value > 0:
            global_limits[key] = value
    settings["limits"] = global_limits

    queue = _as_dict(src.get("queue"))
    settings["queue"]["notify_on_failure"] = _as_bool(
        queue.get("notify_on_failure"), True
    )

    # v1 → v2：把物理模型 key 变成 catalog id（幂等，已迁移过不会重复建条目）
    migrate_legacy_defaults(settings, providers)
    return settings
