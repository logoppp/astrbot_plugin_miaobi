"""妙笔 · 分辨率解析与三层兜底。

请求可以是三种写法：
    832x1216        精确尺寸
    3:4             比例（按模型默认像素量或 1MP 推算）
    3:4@1.5MP       比例 + 目标像素量

解析结果按「模型 limits → 协议 limits → 全局 limits」逐层收紧，
最终一定落在合法区间内，并在 notes 里说明被调整的原因。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

_SIZE_RE = re.compile(r"^\s*(\d{1,5})\s*[x*×]\s*(\d{1,5})\s*$", re.IGNORECASE)
_RATIO_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*[:/]\s*(\d{1,3}(?:\.\d+)?)\s*$")
_MP_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*(?:mp|mpx|megapixels?)\s*$", re.IGNORECASE)
_RATIO_MP_RE = re.compile(
    r"^\s*(\d{1,3}(?:\.\d+)?)\s*[:/]\s*(\d{1,3}(?:\.\d+)?)\s*@\s*(\d{1,3}(?:\.\d+)?)\s*(?:mp|mpx|megapixels?)?\s*$",
    re.IGNORECASE,
)

# 常见比例 → 基准尺寸（约 1MP，SDXL 时代的通用档）
PRESET_RATIOS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "3:4": (896, 1152),
    "4:3": (1152, 896),
    "9:16": (768, 1344),
    "16:9": (1344, 768),
    "2:3": (832, 1248),
    "3:2": (1248, 832),
    "21:9": (1536, 640),
    "9:21": (640, 1536),
    "1:2": (704, 1408),
    "2:1": (1408, 704),
}

DEFAULT_PIXELS = 1024 * 1024


@dataclass
class SizeResult:
    width: int
    height: int
    requested: str = ""
    adjusted: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def ratio(self) -> str:
        g = math.gcd(self.width, self.height) or 1
        return f"{self.width // g}:{self.height // g}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "pixels": self.pixels,
            "ratio": self.ratio,
            "requested": self.requested,
            "adjusted": self.adjusted,
            "notes": list(self.notes),
        }


def preset_choices() -> list[dict[str, Any]]:
    """给 UI 用的预置档位：每个比例给 1MP / 1.5MP / 2MP 三档。"""
    out: list[dict[str, Any]] = []
    for label, (base_w, base_h) in PRESET_RATIOS.items():
        for mp in (1.0, 1.5, 2.0):
            result = _fit(base_w / base_h, mp * 1024 * 1024, 8)
            out.append(
                {
                    "ratio": label,
                    "megapixels": mp,
                    "size": f"{result[0]}x{result[1]}",
                    "label": f"{label} · {mp:g}MP · {result[0]}x{result[1]}",
                }
            )
    return out


def _simplify_ratio(left: float, right: float) -> str:
    """把 6:8 这类比例化简成 3:4，用来匹配预置档位。"""
    try:
        a, b = int(round(float(left))), int(round(float(right)))
    except (TypeError, ValueError):
        return ""
    if a <= 0 or b <= 0:
        return ""
    g = math.gcd(a, b) or 1
    return f"{a // g}:{b // g}"


def _fit(aspect: float, pixels: float, align: int) -> tuple[int, int]:
    """按面积与比例算尺寸，对齐到 align 的整数倍（向上取整更接近目标面积）。"""
    height = math.sqrt(max(pixels, 1.0) / max(aspect, 1e-6))
    width = height * aspect
    step = max(1, int(align))
    w = max(step, int(round(width / step)) * step)
    h = max(step, int(round(height / step)) * step)
    return w, h


def _merge_limits(*layers: dict[str, Any] | None) -> dict[str, Any]:
    """逐层收紧：min 取最大、max 取最小，align 取最大，ratios 取交集（空视为不限）。"""
    merged: dict[str, Any] = {
        "min_side": 1,
        "max_side": 100000,
        "min_pixels": 0,
        "max_pixels": 0,
        "align": 8,
        "ratios": [],
        "min_duration": 0,
        "max_duration": 0,
    }
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        merged["min_side"] = max(int(merged["min_side"]), int(layer.get("min_side") or 0))
        max_side = int(layer.get("max_side") or 0)
        if max_side > 0:
            merged["max_side"] = min(int(merged["max_side"]), max_side)
        merged["min_pixels"] = max(int(merged["min_pixels"]), int(layer.get("min_pixels") or 0))
        max_pixels = int(layer.get("max_pixels") or 0)
        if max_pixels > 0:
            merged["max_pixels"] = (
                max_pixels
                if not merged["max_pixels"]
                else min(int(merged["max_pixels"]), max_pixels)
            )
        merged["align"] = max(int(merged["align"]), int(layer.get("align") or 1))
        ratios = [str(r) for r in (layer.get("ratios") or []) if str(r).strip()]
        if ratios:
            if merged["ratios"]:
                common = [r for r in merged["ratios"] if r in ratios]
                # 交集为空时保留上一层：宁可放宽比例，也不要把可用集合收成空
                merged["ratios"] = common or merged["ratios"]
            else:
                merged["ratios"] = ratios
        merged["min_duration"] = max(
            int(merged["min_duration"]), int(layer.get("min_duration") or 0)
        )
        max_duration = int(layer.get("max_duration") or 0)
        if max_duration > 0:
            merged["max_duration"] = (
                max_duration
                if not merged["max_duration"]
                else min(int(merged["max_duration"]), max_duration)
            )
    if merged["max_side"] < merged["min_side"]:
        merged["max_side"] = merged["min_side"]
    if merged["max_pixels"] and merged["max_pixels"] < merged["min_pixels"]:
        merged["max_pixels"] = merged["min_pixels"]
    return merged


def _nearest_ratio(aspect: float, choices: list[str]) -> str:
    best = choices[0]
    best_delta = None
    for item in choices:
        match = _RATIO_RE.match(item)
        if not match:
            continue
        value = float(match.group(1)) / max(float(match.group(2)), 1e-6)
        delta = abs(math.log(value / aspect))
        if best_delta is None or delta < best_delta:
            best, best_delta = item, delta
    return best


def resolve_size(
    request: str | None,
    *,
    model_limits: dict[str, Any] | None = None,
    protocol_limits: dict[str, Any] | None = None,
    global_limits: dict[str, Any] | None = None,
    default_pixels: float = DEFAULT_PIXELS,
) -> SizeResult:
    """把任意写法解析成合法尺寸。"""
    raw = str(request or "").strip()
    limits = _merge_limits(model_limits, protocol_limits, global_limits)
    align = max(1, int(limits["align"]))

    width = height = 0
    target_pixels = float(default_pixels)
    aspect = 1.0
    pure_ratio = ""

    match = _RATIO_MP_RE.match(raw)
    if match:
        aspect = float(match.group(1)) / max(float(match.group(2)), 1e-6)
        target_pixels = float(match.group(3)) * 1024 * 1024
    else:
        match = _SIZE_RE.match(raw)
        if match:
            width, height = int(match.group(1)), int(match.group(2))
            aspect = width / max(height, 1)
            target_pixels = float(width * height)
        else:
            match = _RATIO_RE.match(raw)
            if match:
                aspect = float(match.group(1)) / max(float(match.group(2)), 1e-6)
                pure_ratio = _simplify_ratio(
                    float(match.group(1)), float(match.group(2))
                )
            else:
                match = _MP_RE.match(raw)
                if match:
                    target_pixels = float(match.group(1)) * 1024 * 1024
                    preset = PRESET_RATIOS.get("1:1")
                    if preset:
                        aspect = preset[0] / preset[1]

    notes: list[str] = []
    allowed = limits.get("ratios") or []
    if allowed:
        nearest = _nearest_ratio(aspect, allowed)
        match = _RATIO_RE.match(nearest)
        if match:
            target_aspect = float(match.group(1)) / max(float(match.group(2)), 1e-6)
            if abs(math.log(target_aspect / aspect)) > 1e-6:
                notes.append(f"比例 {raw or '默认'} 不在允许列表，改用 {nearest}")
            aspect = target_aspect

    if width and height:
        # 精确尺寸：只做 clamp，不重算比例
        pass
    elif pure_ratio and pure_ratio in PRESET_RATIOS:
        # 纯比例请求优先用标准档位，避免算出 887x1182 这种非标尺寸
        width, height = PRESET_RATIOS[pure_ratio]
    else:
        width, height = _fit(aspect, target_pixels, align)

    def _clamp(w: int, h: int) -> tuple[int, int]:
        min_side = int(limits["min_side"])
        max_side = int(limits["max_side"])
        # 单边超限：按最长边整体缩
        longest = max(w, h)
        if longest > max_side:
            factor = max_side / float(longest)
            w, h = int(w * factor), int(h * factor)
        shortest = min(w, h)
        if shortest < min_side:
            factor = min_side / float(max(shortest, 1))
            w, h = int(w * factor), int(h * factor)
        # 面积约束
        max_pixels = int(limits["max_pixels"] or 0)
        if max_pixels and w * h > max_pixels:
            factor = math.sqrt(max_pixels / float(w * h))
            w, h = int(w * factor), int(h * factor)
        min_pixels = int(limits["min_pixels"] or 0)
        if min_pixels and w * h < min_pixels:
            factor = math.sqrt(min_pixels / float(w * h))
            w, h = int(w * factor), int(h * factor)
        # 对齐（向下对齐，避免又超上限）
        w = max(align, int(w / align) * align)
        h = max(align, int(h / align) * align)
        # 对齐后可能再次越界，迭代收敛
        for _ in range(6):
            if max_pixels and w * h > max_pixels:
                w = max(align, (w - align) // align * align)
                h = max(align, (h - align) // align * align)
                continue
            if max(w, h) > max_side:
                factor = max_side / float(max(w, h))
                w, h = int(w * factor), int(h * factor)
                w = max(align, w // align * align)
                h = max(align, h // align * align)
                continue
            if min(w, h) < min_side:
                factor = min_side / float(max(min(w, h), 1))
                w, h = int(w * factor), int(h * factor)
                w = max(align, int(w / align) * align)
                h = max(align, int(h / align) * align)
                continue
            break
        return max(align, w), max(align, h)

    original = (width, height)
    width, height = _clamp(width, height)

    if (width, height) != original:
        notes.append(f"{original[0]}x{original[1]} 超出该模型限制，已调整为 {width}x{height}")
    if align > 1 and (width % align or height % align):  # pragma: no cover - 理论上不会发生
        notes.append(f"尺寸已对齐到 {align} 的倍数")

    return SizeResult(
        width=width,
        height=height,
        requested=raw,
        adjusted=(width, height) != original or bool(notes),
        notes=notes,
    )


def resolve_duration(
    request: Any,
    *,
    model_limits: dict[str, Any] | None = None,
    default: int = 5,
) -> tuple[int, list[str]]:
    """时长按模型上下限夹紧，返回 (秒, notes)。"""
    notes: list[str] = []
    try:
        value = int(float(str(request).strip().rstrip("sS秒")))
    except (TypeError, ValueError):
        value = int(default)
    limits = model_limits or {}
    low = int(limits.get("min_duration") or 0)
    high = int(limits.get("max_duration") or 0)
    if low and value < low:
        notes.append(f"时长 {value}s 低于下限，改为 {low}s")
        value = low
    if high and value > high:
        notes.append(f"时长 {value}s 超过上限，改为 {high}s")
        value = high
    return value, notes
