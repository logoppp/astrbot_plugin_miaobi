"""尺寸解析的回归测试。

`resolve_size` 决定最终发给上游的 width/height：算错要么 400（模型不认这个尺寸），
要么被静默裁图。它同时是纯函数，改动成本最低、回归代价最高，所以放第一道防线。
"""

from __future__ import annotations

import math

import pytest

from core.sizing import (
    PRESET_RATIOS,
    SizeResult,
    _merge_limits,
    preset_choices,
    resolve_duration,
    resolve_size,
)

ALIGN = 8
ONE_MP = 1024 * 1024


# ---------------------------------------------------------------- 输入写法


def test_explicit_size_passthrough():
    result = resolve_size("832x1216")
    assert (result.width, result.height) == (832, 1216)
    assert result.adjusted is False
    assert result.notes == []


@pytest.mark.parametrize("text", ["832x1216", "832*1216", "832X1216", " 832 x 1216 "])
def test_explicit_size_accepts_common_separators(text):
    result = resolve_size(text)
    assert (result.width, result.height) == (832, 1216)


@pytest.mark.parametrize(
    "label,expected",
    sorted(
        (key, value)
        for key, value in PRESET_RATIOS.items()
        if math.gcd(*(int(part) for part in key.split(":"))) == 1
    ),
)
def test_canonical_ratio_uses_exact_preset(label, expected):
    """最简比例直接命中标准档位，不能算出 887x1182 这种非标尺寸。"""
    result = resolve_size(label)
    assert (result.width, result.height) == expected


@pytest.mark.parametrize("label", ["21:9", "9:21"])
def test_non_canonical_ratio_key_still_preserves_aspect(label):
    """`21:9` 会被化简成 `7:3`，查不到档位就走面积拟合——比例仍要保住。"""
    left, right = (float(part) for part in label.split(":"))
    result = resolve_size(label)
    assert abs(result.width / result.height - left / right) / (left / right) < 0.02


def test_ratio_with_megapixels_hits_target_area():
    result = resolve_size("3:4@1.5")
    assert abs(result.width / result.height - 0.75) < 0.02
    target = 1.5 * ONE_MP
    assert abs(result.width * result.height - target) / target < 0.05


def test_megapixels_default_to_square():
    result = resolve_size("2mp")
    assert abs(result.width / result.height - 1.0) < 0.01
    target = 2 * ONE_MP
    assert abs(result.width * result.height - target) / target < 0.05


@pytest.mark.parametrize("text", ["", None, "   ", "abc", "随便写点什么"])
def test_garbage_input_falls_back_to_default_square(text):
    """脏输入不能崩，退回默认 1MP 方图。"""
    result = resolve_size(text)
    assert (result.width, result.height) == (1024, 1024)
    assert result.adjusted is False


def test_zero_size_only_needs_to_stay_sane():
    """`0x0` 是退化输入：只保证不崩、尺寸为正且对齐，不保证被当成默认值。"""
    result = resolve_size("0x0")
    assert result.width > 0 and result.height > 0
    assert result.width % ALIGN == 0 and result.height % ALIGN == 0


@pytest.mark.parametrize("text", ["832x1216", "3:4@1.5", "2mp", "17:5", "1:1", "21:9"])
def test_output_is_always_aligned(text):
    result = resolve_size(text)
    assert result.width % ALIGN == 0, text
    assert result.height % ALIGN == 0, text


# ---------------------------------------------------------------- 上限约束


def test_max_side_is_clamped_with_note():
    result = resolve_size("4096x4096", model_limits={"max_side": 2048})
    assert max(result.width, result.height) <= 2048
    assert result.adjusted is True
    assert any("4096x4096" in note and "已调整" in note for note in result.notes)


def test_max_pixels_is_clamped():
    result = resolve_size("2048x2048", model_limits={"max_pixels": ONE_MP})
    assert result.width * result.height <= ONE_MP


def test_min_side_is_raised():
    result = resolve_size("64x64", model_limits={"min_side": 512})
    assert min(result.width, result.height) >= 512


def test_min_pixels_is_raised():
    result = resolve_size("64x64", model_limits={"min_pixels": 512 * 512})
    assert result.width * result.height >= 512 * 512


# ---------------------------------------------------------------- 比例白名单


@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知缺陷（等授权修复）：resolve_size 先把 aspect 改成白名单里的 1:1，"
        "但 pure_ratio 仍是用户输入的 16:9，后面的 `pure_ratio in PRESET_RATIOS` "
        "分支又把尺寸改回 1344x768 —— notes 声称已改用 1:1，实际没有，上游会 400。"
        "修法：白名单改写 aspect 时同步清空 pure_ratio。"
    ),
)
def test_ratio_whitelist_snaps_to_allowed():
    """模型只允许 1:1 时，16:9 必须真的变成 1:1，否则上游直接 400。"""
    result = resolve_size("16:9", model_limits={"ratios": ["1:1"]})
    assert any("比例" in note for note in result.notes)
    assert abs(result.width / result.height - 1.0) < 0.02, (
        f"要求 1:1，实际 {result.width}x{result.height}"
    )


def test_ratio_whitelist_picks_nearest():
    result = resolve_size("4:5", model_limits={"ratios": ["3:4", "9:16"]})
    assert abs(result.width / result.height - 0.75) < 0.02


def test_ratio_whitelist_keeps_requested_when_allowed():
    result = resolve_size("16:9", model_limits={"ratios": ["16:9", "1:1"]})
    assert (result.width, result.height) == PRESET_RATIOS["16:9"]
    assert result.notes == []


# ---------------------------------------------------------------- 限制合并


def test_merge_limits_tightens_each_field():
    merged = _merge_limits(
        {"min_side": 256, "max_side": 4096, "align": 8, "ratios": ["1:1", "3:4"]},
        {"min_side": 512, "max_side": 2048, "align": 16, "ratios": ["3:4", "16:9"]},
        None,
    )
    assert merged["min_side"] == 512
    assert merged["max_side"] == 2048
    assert merged["align"] == 16
    assert merged["ratios"] == ["3:4"]


def test_merge_limits_empty_intersection_keeps_previous():
    """交集为空时保留上一层：宁可放宽比例，也不要把可用集合收成空。"""
    merged = _merge_limits({"ratios": ["16:9"]}, {"ratios": ["9:16"]})
    assert merged["ratios"] == ["16:9"]


def test_merge_limits_repairs_inverted_bounds():
    merged = _merge_limits({"min_side": 4096, "max_side": 512})
    assert merged["max_side"] >= merged["min_side"]


# ---------------------------------------------------------------- 时长


def test_duration_in_range_is_untouched():
    assert resolve_duration(8, model_limits={"max_duration": 10}) == (8, [])


def test_duration_is_clamped_both_ways():
    value, notes = resolve_duration(30, model_limits={"max_duration": 10})
    assert value == 10 and notes
    value, notes = resolve_duration(1, model_limits={"min_duration": 5})
    assert value == 5 and notes


@pytest.mark.parametrize("text", ["8", "8s", "8S", "8秒"])
def test_duration_parses_common_suffixes(text):
    assert resolve_duration(text)[0] == 8


def test_duration_garbage_uses_default():
    assert resolve_duration("abc", default=5)[0] == 5


# ---------------------------------------------------------------- 结果对象


def test_size_result_properties():
    result = SizeResult(width=832, height=1248)
    assert result.size == "832x1248"
    assert result.ratio == "2:3"
    assert result.pixels == 832 * 1248
    assert result.as_dict()["size"] == "832x1248"


def test_preset_choices_cover_three_scales_per_ratio():
    choices = preset_choices()
    assert len(choices) == len(PRESET_RATIOS) * 3
    for item in choices:
        assert item["size"].count("x") == 1
        assert "·" in item["label"]
