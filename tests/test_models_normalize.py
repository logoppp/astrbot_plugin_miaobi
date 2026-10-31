"""配置规范化的回归测试。

这些函数决定「读进来的 JSON 变成什么」。写错的代价不是报错，而是**静默丢用户数据**：
2026-10-31 已经因为 BOM 和「预设整份重播」丢过两次（提示词 4 条、NAI 画师串 12 条）。
所以这里重点守三件事：幂等、只补缺不覆盖、同一模型多供应商合并。
"""

from __future__ import annotations

import json

from core.models import (
    CAP_I2I,
    CAP_T2I,
    CAP_T2V,
    index_physical_models,
    migrate_legacy_defaults,
    normalize_catalog,
    normalize_catalog_entry,
    normalize_limits,
    normalize_model,
    normalize_presets,
    normalize_settings,
    slugify,
)


# ---------------------------------------------------------------- slug / 条目


def test_slugify_handles_path_style_model_id():
    assert slugify("black-forest-labs/FLUX.1-dev") == "black-forest-labs-flux-1-dev"
    assert slugify("   ") == ""
    assert len(slugify("a" * 200)) <= 48


def test_catalog_entry_defaults_capability_to_t2i():
    assert normalize_catalog_entry({"id": "x"})["capabilities"] == [CAP_T2I]
    # 非法能力被剔除；剔空后回落到 t2i
    assert normalize_catalog_entry({"id": "x", "capabilities": ["nope"]})["capabilities"] == [CAP_T2I]
    assert normalize_catalog_entry({"id": "x", "capabilities": ["t2v", "bogus"]})["capabilities"] == [CAP_T2V]


def test_catalog_entry_splits_legacy_tag_family():
    """v2 早期把 NAI 和 Anima 合称 tag，读入时必须按名字拆回各自家族。"""
    anima = normalize_catalog_entry({"id": "anima-2-9b", "name": "Anima 2.9B", "family": "tag"})
    nai = normalize_catalog_entry({"id": "nai-4-5", "name": "NovelAI V4.5", "family": "tag"})
    assert anima["family"] == "anima"
    assert nai["family"] == "nai"


def test_catalog_entry_keeps_explicit_family():
    assert normalize_catalog_entry({"id": "x", "family": "qwen-image"})["family"] == "qwen-image"


def test_catalog_entry_dedupes_chain_and_accepts_shorthand():
    entry = normalize_catalog_entry(
        {
            "id": "x",
            "chain": [
                "p1/m1",
                "p1/m1",
                {"provider_id": "p2", "model_id": "m2"},
                {"provider_id": "", "model_id": ""},
                "",
            ],
        }
    )
    assert entry["chain"] == [
        {"provider_id": "p1", "model_id": "m1"},
        {"provider_id": "p2", "model_id": "m2"},
    ]


def test_catalog_entry_treats_bare_name_as_provider_only_binding():
    """没有斜杠的简写按「这家供应商的任意模型」处理，别当脏数据丢掉。"""
    entry = normalize_catalog_entry({"id": "x", "chain": ["p_only"]})
    assert entry["chain"] == [{"provider_id": "p_only", "model_id": ""}]


def test_normalize_catalog_renames_duplicate_ids():
    out = normalize_catalog([{"id": "same"}, {"id": "same"}, {"id": "same"}])
    assert [item["id"] for item in out] == ["same", "same-2", "same-3"]


def test_normalize_catalog_skips_non_dict():
    assert normalize_catalog(["x", 1, None]) == []


# ---------------------------------------------------------------- 模型/供应商


def test_normalize_model_drops_dangling_default_preset():
    """默认预设必须真实存在，否则生成时会静默失效。"""
    model = normalize_model(
        {"id": "m", "model_id": "x", "default_preset_id": "missing", "prompt_presets": []}
    )
    assert model["default_preset_id"] == ""

    kept = normalize_model(
        {
            "id": "m",
            "model_id": "x",
            "default_preset_id": "ps1",
            "prompt_presets": [{"id": "ps1", "name": "n", "prompt": "p"}],
        }
    )
    assert kept["default_preset_id"] == "ps1"


def test_normalize_model_falls_back_to_provider_id():
    model = normalize_model({"model_id": "x"}, "p_1")
    assert model["provider_id"] == "p_1"
    assert model["display_name"] == "x"


def test_normalize_presets_generates_ids_and_fixes_mode():
    presets = normalize_presets(
        [
            {"name": "a", "prompt": "x", "mode": "乱写"},
            {"name": "", "prompt": ""},
            "not-a-dict",
        ]
    )
    assert len(presets) == 2
    assert presets[0]["mode"] == "append"
    assert presets[0]["id"].startswith("ps")
    # 正文允许为空：只填负面词的预设不能被丢掉
    assert presets[1]["name"] == "未命名预设"


def test_normalize_limits_repairs_inverted_bounds():
    limits = normalize_limits({"min_side": 4096, "max_side": 512, "align": 0})
    assert limits["max_side"] >= limits["min_side"]
    assert limits["align"] >= 1


# ---------------------------------------------------------------- settings


def test_normalize_settings_fills_defaults():
    settings = normalize_settings({})
    assert settings["version"] == 2
    assert settings["defaults"] == {CAP_T2I: "", CAP_I2I: "", CAP_T2V: ""}
    assert settings["optimizer"]["on_error"] == "use_original"
    assert settings["optimizer"]["enabled"] is True


def test_normalize_settings_promotes_legacy_llm_provider():
    """旧版只有一个 llm_provider_id，要自动并进回退链。"""
    settings = normalize_settings({"optimizer": {"llm_provider_id": "qwenox/qwen3"}})
    assert settings["optimizer"]["llm_providers"] == ["qwenox/qwen3"]
    assert settings["optimizer"]["llm_provider_id"] == "qwenox/qwen3"


def test_normalize_settings_prefers_explicit_chain():
    settings = normalize_settings(
        {
            "optimizer": {
                "llm_providers": ["a/1", "b/2"],
                "llm_provider_id": "",
            }
        }
    )
    assert settings["optimizer"]["llm_providers"] == ["a/1", "b/2"]
    assert settings["optimizer"]["llm_provider_id"] == "a/1"


def test_normalize_settings_normalizes_legacy_profile_family():
    settings = normalize_settings(
        {"optimizer": {"profiles": [{"family": "tag", "system_prompt": "x"}]}}
    )
    assert settings["optimizer"]["profiles"][0]["family"] == "nai"


# ---------------------------------------------------------------- 迁移


def _providers() -> list[dict]:
    """两家供应商都提供同一个 model_id —— 正是回退链要合并的场景。"""
    return [
        {
            "id": "p_a",
            "name": "甲",
            "protocol": "openai_images",
            "models": [
                {
                    "id": "mA",
                    "model_id": "flux-dev",
                    "display_name": "FLUX Dev",
                    "capabilities": ["t2i", "i2i"],
                }
            ],
        },
        {
            "id": "p_b",
            "name": "乙",
            "protocol": "openai_images",
            "models": [
                {
                    "id": "mB",
                    "model_id": "flux-dev",
                    "display_name": "FLUX Dev",
                    "capabilities": ["t2i", "i2i"],
                }
            ],
        },
    ]


def test_index_physical_models_maps_key_to_binding():
    index = index_physical_models(_providers())
    assert index["mA"]["provider_id"] == "p_a"
    assert index["mB"]["model_id"] == "flux-dev"


def test_migrate_wraps_legacy_default_into_catalog():
    providers = _providers()
    settings = normalize_settings({"defaults": {"t2i": "mA"}}, providers)
    assert settings["defaults"]["t2i"] == "flux-dev"
    entry = next(item for item in settings["catalog"] if item["id"] == "flux-dev")
    assert entry["chain"] == [{"provider_id": "p_a", "model_id": "flux-dev"}]


def test_migrate_is_idempotent():
    providers = _providers()
    settings = normalize_settings({"defaults": {"t2i": "mA"}, "catalog": []}, providers)
    snapshot = json.dumps(settings, sort_keys=True, ensure_ascii=False)
    migrate_legacy_defaults(settings, providers)
    migrate_legacy_defaults(settings, providers)
    assert json.dumps(settings, sort_keys=True, ensure_ascii=False) == snapshot


def test_migrate_merges_same_model_across_providers():
    """同一 model_id 的多家供应商必须落进同一条 chain，不能建两条 catalog。"""
    providers = _providers()
    settings = normalize_settings(
        {"defaults": {"t2i": "mA"}, "fallback_chain": {"t2i": ["mB"]}},
        providers,
    )
    flux = [item for item in settings["catalog"] if item["id"] == "flux-dev"]
    assert len(flux) == 1, "同一模型被拆成了多条 catalog"
    assert flux[0]["chain"] == [
        {"provider_id": "p_a", "model_id": "flux-dev"},
        {"provider_id": "p_b", "model_id": "flux-dev"},
    ]


def test_migrate_keeps_unknown_default_untouched():
    """映射不到就原样留着交给界面提示，**不能静默清空**。"""
    settings = normalize_settings({"defaults": {"t2i": "已经删掉的模型"}}, [])
    migrate_legacy_defaults(settings, [])
    assert settings["defaults"]["t2i"] == "已经删掉的模型"
