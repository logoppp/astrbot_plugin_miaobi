"""妙笔 · 媒体落盘、下载与元数据 sidecar。"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from astrbot.api import logger

try:
    from PIL import Image

    _HAS_PIL = True
except Exception:  # noqa: BLE001
    Image = None  # type: ignore[assignment]


# 单个媒体文件上限：再大就不是正常的出图结果了，多半是配置错误或恶意地址
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


def _blocked_host(host: str) -> bool:
    """拦掉没有任何正常用途的地址。

    刻意**不拦** 127.0.0.1 与 192.168 / 10 / 172.16 段：
    本地 ComfyUI 返回的结果图就落在这些地址上，拦了会直接打断自建场景。
    真正要防的是云元数据地址（169.254.169.254）和组播/保留段。
    """
    name = (host or "").strip().strip("[]").lower()
    if not name:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return ip.is_link_local or ip.is_multicast or ip.is_reserved or str(ip) == "0.0.0.0"
    _HAS_PIL = False

_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}


def decode_data_url(value: str) -> tuple[bytes, str]:
    """解析 data:image/png;base64,xxxx → (字节, mime)。

    上传的参考图走这条路径：浏览器把文件读成 data URL，服务端直接解码，
    不需要额外落盘一次临时文件。
    """
    text = str(value or "").strip()
    if not text.startswith("data:"):
        raise ValueError("不是 data URL")
    header, _, payload = text[5:].partition(",")
    if not payload:
        raise ValueError("data URL 缺少内容")
    # base64 体积约为原文件的 4/3，超限直接拒绝，避免一次性解码吃满内存
    if len(payload) > MAX_DOWNLOAD_BYTES * 4 // 3 + 64:
        raise ValueError(f"上传内容过大（上限 {MAX_DOWNLOAD_BYTES} 字节）")
    mime = header.split(";")[0].strip().lower() or "application/octet-stream"
    if "base64" in header.lower():
        import base64

        try:
            return base64.b64decode(payload, validate=False), mime
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"base64 解码失败: {exc}") from exc
    from urllib.parse import unquote_to_bytes

    return unquote_to_bytes(payload), mime


def guess_ext(data: bytes, mime: str = "", fallback: str = ".png") -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    if mime in _MIME_EXT:
        return _MIME_EXT[mime]
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:12] in (b"ftypisom", b"ftypmp42", b"ftypavc1"):
        return ".mp4"
    return fallback


def sniff_mime(path: Path) -> str:
    ext = path.suffix.lower()
    for mime, suffix in _MIME_EXT.items():
        if suffix == ext:
            return mime
    return "application/octet-stream"


class MediaStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- 路径 ----------

    def month_dir(self, kind: str = "image") -> Path:
        bucket = "videos" if kind == "video" else "images"
        target = self.root / bucket / time.strftime("%Y-%m")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def thumb_dir(self) -> Path:
        target = self.root / "thumbs"
        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def new_name(prefix: str, ext: str) -> str:
        # 同一秒内同协议并发出图很常见，只靠时间戳会互相覆盖，必须带随机串
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return "%s_%s_%s%s" % (prefix, stamp, secrets.token_hex(3), ext)

    # ---------- 下载 ----------

    async def download(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        timeout: int = 300,
    ) -> tuple[bytes, str]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise RuntimeError(f"不支持的下载地址: {url[:80]}")
        if _blocked_host(parts.hostname or ""):
            raise RuntimeError(f"拒绝下载该地址: {url[:80]}")
        client_timeout = aiohttp.ClientTimeout(total=float(timeout))
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(url, headers=headers or {}, proxy=proxy) as response:
                if response.status >= 400:
                    text = (await response.read())[:200].decode("utf-8", "replace")
                    raise RuntimeError(f"下载失败 HTTP {response.status}: {text}")
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(
                        f"文件过大（{int(declared)} 字节，上限 {MAX_DOWNLOAD_BYTES}）"
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError(f"下载超过 {MAX_DOWNLOAD_BYTES} 字节上限")
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("Content-Type", "")

    # ---------- 保存 ----------

    def save(
        self,
        data: bytes,
        *,
        kind: str = "image",
        mime: str = "",
        prefix: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ext = guess_ext(data, mime, ".mp4" if kind == "video" else ".png")
        folder = self.month_dir(kind)
        stem = prefix or ("vid" if kind == "video" else "img")
        path = folder / self.new_name(stem, ext)
        # 极端情况下（随机串撞车或外部已存在同名文件）再退让一次，绝不覆盖已有作品
        for _ in range(5):
            if not path.exists():
                break
            path = folder / self.new_name(stem, ext)
        path.write_bytes(data)

        info: dict[str, Any] = {
            "path": str(path),
            "bytes": len(data),
            "mime": mime or sniff_mime(path),
            "width": 0,
            "height": 0,
            "kind": kind,
            "sha256": hashlib.sha256(data).hexdigest()[:16],
        }

        if kind == "image" and _HAS_PIL:
            try:
                with Image.open(io.BytesIO(data)) as image:
                    info["width"], info["height"] = image.size
                    if image.format and image.format.lower() not in ("png", "jpeg", "webp", "gif"):
                        info["mime"] = mime or "image/png"
            except Exception as exc:  # noqa: BLE001
                logger.debug("[妙笔] 读取图片尺寸失败: %s", exc)

        thumb = self._make_thumb(path, kind)
        if thumb:
            info["thumb_path"] = str(thumb)

        if meta is not None:
            self.write_sidecar(path, meta)
        return info

    def _make_thumb(self, path: Path, kind: str) -> Path | None:
        if kind != "image" or not _HAS_PIL:
            return None
        try:
            with Image.open(path) as image:
                image = image.convert("RGB")
                image.thumbnail((480, 480), Image.LANCZOS)
                target = self.thumb_dir() / (path.stem + ".jpg")
                image.save(target, format="JPEG", quality=82)
                return target
        except Exception as exc:  # noqa: BLE001
            logger.debug("[妙笔] 生成缩略图失败: %s", exc)
            return None

    # ---------- sidecar ----------

    @staticmethod
    def write_sidecar(path: Path, meta: dict[str, Any]) -> None:
        try:
            sidecar = path.with_suffix(path.suffix + ".json")
            sidecar.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[妙笔] 写入元数据失败: %s", exc)

    @staticmethod
    def read_sidecar(path: Path) -> dict[str, Any]:
        sidecar = path.with_suffix(path.suffix + ".json")
        if not sidecar.exists():
            return {}
        try:
            return json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    # ---------- 清理 ----------

    def remove(self, path: str) -> None:
        try:
            target = Path(path)
            target.unlink(missing_ok=True)
            sidecar = target.with_suffix(target.suffix + ".json")
            sidecar.unlink(missing_ok=True)
            thumb = self.thumb_dir() / (target.stem + ".jpg")
            thumb.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[妙笔] 删除文件失败 %s: %s", path, exc)
