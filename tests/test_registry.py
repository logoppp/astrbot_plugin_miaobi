"""catalog 解析与同名消歧的回归测试。

`ModelRegistry` 是「用户写了模型名 → 到底调哪家供应商」的唯一入口。
解析错的后果是发到错误的供应商，或者同名模型随机命中（用户会以为「回退链没生效」）。
"""

from __future__ import annotations

import pytest

pytest.importorskip("astrbot", reason="registry 依赖 astrbot.api.logger，需在容器内跑")

from core.registry import ModelRegistry, norm_alias  # noqa: E402


def _providers() -> list[dict]:
    return [
        {
            "id": "p_a",
            "name": "千问",
            "protocol": "openai_images",
            "enabled": True,
            "models": [
                {
                    "id": "ma1",
                    "model_id": "qwen-image-2.0",
                    "display_name": "Qwen Image 2.0",
                    "capabilities": ["t2i", "i2i"],
                    "aliases": ["千问画图"],
                },
                {
                    "id": "ma2",
                    "model_id": "qwen-image-3.0",
                    "display_name": "Qwen Image 3.0",
                    "capabilities": ["t2i"],
                },
            ],
        },
        {
            "id": "p_b",
            "name": "备用站",
            "protocol": "openai_images",
            "enabled": True,
            "models": [
                {
                    "id": "mb1",
                    "model_id": "qwen-image-2.0",
                    "display_name": "Qwen Image 2.0",
                    "capabilities": ["t2i", "i2i"],
                }
            ],
        },
        {
            "id": "p_off",
            "name": "停用站",
            "protocol": "openai_images",
            "enabled": False,
            "models": [{"id": "mo1", "model_id": "gone", "capabilities": ["t2i"]}],
        },
        {
            "id": "p_off_model",
            "name": "半停用站",
            "protocol": "openai_images",
            "enabled": True,
            "models": [
                {"id": "mm1", "model_id": "hidden", "capabilities": ["t2i"], "enabled": False}
            ],
        },
        {
            "id": "p_bad",
            "name": "未知协议",
            "protocol": "不存在的协议",
            "enabled": True,
            "models": [{"id": "mz", "model_id": "whatever", "capabilities": ["t2i"]}],
        },
    ]


@pytest.fixture()
def registry() -> ModelRegistry:
    return ModelRegistry(_providers())


# ---------------------------------------------------------------- 可见性


def test_disabled_provider_is_invisible(registry):
    assert registry.get("mo1") is None


def test_disabled_model_is_invisible(registry):
    assert registry.get("mm1") is None


def test_unknown_protocol_is_skipped_not_fatal(registry):
    """未知协议只跳过那一条，不能让整个注册表空掉。"""
    assert registry.get("mz") is None
    assert len(registry.refs) == 3


# ---------------------------------------------------------------- 候选解析


def test_candidates_exact_key(registry):
    hits = registry.candidates("ma1")
    assert [ref.key for ref in hits] == ["ma1"]


def test_candidates_matches_alias(registry):
    hits = registry.candidates("千问画图")
    assert [ref.key for ref in hits] == ["ma1"]


def test_candidates_matches_model_id_across_providers(registry):
    """同名模型落在两家供应商时，两条都要返回，交给上层消歧。"""
    hits = registry.candidates("qwen-image-2.0")
    assert {ref.key for ref in hits} == {"ma1", "mb1"}


def test_candidates_provider_scoped(registry):
    hits = registry.candidates("千问/qwen-image-2.0")
    assert [ref.key for ref in hits] == ["ma1"]


def test_candidates_respects_capability(registry):
    assert registry.candidates("qwen-image-3.0", "i2i") == []
    assert [ref.key for ref in registry.candidates("qwen-image-3.0", "t2i")] == ["ma2"]


def test_candidates_prefix_fallback(registry):
    hits = registry.candidates("qwen-image")
    assert {ref.key for ref in hits} == {"ma1", "ma2", "mb1"}


def test_candidates_empty_token(registry):
    assert registry.candidates("") == []


def test_resolve_single_hit(registry):
    ref = registry.resolve("千问画图")
    assert ref is not None and ref.key == "ma1"


def test_resolve_ambiguous_prefers_configured_order(registry):
    ref = registry.resolve("qwen-image-2.0")
    assert ref is not None and ref.provider_id == "p_a"


def test_norm_alias_strips_separators():
    assert norm_alias("Qwen-Image_2.0 / XL") == "qwenimage2.0xl"


# ---------------------------------------------------------------- chain


def test_chain_refs_follows_chain_order(registry):
    entry = {
        "id": "flux",
        "chain": [
            {"provider_id": "p_b", "model_id": "qwen-image-2.0"},
            {"provider_id": "p_a", "model_id": "qwen-image-2.0"},
        ],
    }
    assert [ref.key for ref in registry.chain_refs(entry, "t2i")] == ["mb1", "ma1"]


def test_chain_refs_filters_missing_capability(registry):
    entry = {
        "id": "flux",
        "chain": [
            {"provider_id": "p_a", "model_id": "qwen-image-3.0"},  # 只有 t2i
            {"provider_id": "p_a", "model_id": "qwen-image-2.0"},
        ],
    }
    assert [ref.key for ref in registry.chain_refs(entry, "i2i")] == ["ma1"]


def test_chain_refs_skips_removed_binding(registry):
    entry = {
        "id": "flux",
        "chain": [
            {"provider_id": "p_off", "model_id": "gone"},
            {"provider_id": "p_a", "model_id": "qwen-image-2.0"},
        ],
    }
    assert [ref.key for ref in registry.chain_refs(entry, "t2i")] == ["ma1"]


def test_all_chain_refs_reports_broken_bindings(registry):
    """界面要能显示「这条绑定已经失效」，所以这里不能过滤掉 None。"""
    entry = {
        "id": "flux",
        "chain": [
            {"provider_id": "p_off", "model_id": "gone"},
            {"provider_id": "p_a", "model_id": "qwen-image-2.0"},
        ],
    }
    pairs = registry.all_chain_refs(entry)
    assert pairs[0][1] is None
    assert pairs[1][1] is not None


# ---------------------------------------------------------------- 默认解析


def test_pick_default_returns_first_chain_ref(registry):
    settings = {
        "defaults": {"t2i": "flux"},
        "catalog": [
            {
                "id": "flux",
                "capabilities": ["t2i"],
                "chain": [
                    {"provider_id": "p_b", "model_id": "qwen-image-2.0"},
                    {"provider_id": "p_a", "model_id": "qwen-image-2.0"},
                ],
            }
        ],
    }
    ref = registry.pick_default("t2i", settings)
    assert ref is not None and ref.key == "mb1"


def test_pick_default_skips_catalog_without_usable_binding(registry):
    settings = {
        "defaults": {"t2i": "broken"},
        "catalog": [
            {
                "id": "broken",
                "capabilities": ["t2i"],
                "chain": [{"provider_id": "p_off", "model_id": "gone"}],
            },
            {
                "id": "ok",
                "capabilities": ["t2i"],
                "chain": [{"provider_id": "p_a", "model_id": "qwen-image-2.0"}],
            },
        ],
    }
    ref = registry.pick_default("t2i", settings)
    assert ref is not None and ref.key == "ma1"


def test_pick_default_ignores_disabled_catalog(registry):
    settings = {
        "defaults": {"t2i": "off"},
        "catalog": [
            {
                "id": "off",
                "enabled": False,
                "capabilities": ["t2i"],
                "chain": [{"provider_id": "p_a", "model_id": "qwen-image-2.0"}],
            }
        ],
    }
    # 停用的 catalog 会被跳过，最后回落到「第一个可用模型」
    ref = registry.pick_default("t2i", settings)
    assert ref is not None and ref.key in {"ma1", "ma2", "mb1"}


def test_resolve_target_accepts_legacy_physical_key(registry):
    """还没迁移的旧配置 defaults 里存的是物理 key，不能因此丢掉默认模型。"""
    entry, refs = registry.resolve_target("t2i", {"defaults": {"t2i": "ma1"}})
    assert [ref.key for ref in refs] == ["ma1"]


def test_resolve_target_unknown_token_returns_empty(registry):
    entry, refs = registry.resolve_target("t2i", {}, token="根本没这个模型")
    assert entry is None and refs == []


def test_resolve_target_does_not_match_other_capability(registry):
    entry, refs = registry.resolve_target("t2v", {"defaults": {"t2v": "qwen-image-2.0"}})
    assert refs == []
