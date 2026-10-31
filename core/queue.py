"""妙笔 · 按供应商分组的任务队列。

- 不同供应商各有独立队列与独立冷却，互不影响，可并行跑；
- 同一供应商内按 concurrency 串行，默认 1；
- 冷却从「上一次完成」起算，mark 在锁内执行（避免下一个任务抢在标记之前放行）；
- 只有真正打到上游并占用配额的失败才计冷却（429 / 5xx / 网络），400/401/403 不计。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from astrbot.api import logger


class QueueFullError(RuntimeError):
    pass


def _should_cooldown(exc: BaseException) -> bool:
    status = int(getattr(exc, "status", 0) or 0)
    if status == 429 or 500 <= status < 600:
        return True
    if status:
        return False
    text = str(exc)
    return any(
        token in text
        for token in ("429", "500", "502", "503", "504", "请求失败", "timeout", "Timeout")
    )


class ProviderQueue:
    def __init__(
        self,
        provider_id: str,
        *,
        concurrency: int = 1,
        cooldown_seconds: float = 0.0,
        max_queue: int = 8,
        state_path: Path | None = None,
    ):
        self.provider_id = provider_id
        self.concurrency = max(1, int(concurrency))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.max_queue = max(1, int(max_queue))
        self.state_path = Path(state_path) if state_path else None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: object | None = None
        self._sem: asyncio.Semaphore | None = None
        self._running = 0
        self._waiting = 0
        self._last_finish = self._load()

    # ---------- 冷却状态 ----------

    def _load(self) -> float:
        if not self.state_path or not self.state_path.exists():
            return 0.0
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return float(payload.get("last_finish") or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"last_finish": self._last_finish}), encoding="utf-8"
            )
            tmp.replace(self.state_path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[妙笔] 队列状态落盘失败 %s: %s", self.provider_id, exc)

    def remaining(self) -> float:
        if self.cooldown_seconds <= 0:
            return 0.0
        return max(0.0, self._last_finish + self.cooldown_seconds - time.time())

    def mark(self) -> None:
        self._last_finish = time.time()
        self._save()

    # ---------- 并发原语 ----------

    def _loop(self) -> object | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _primitive(self):
        loop = self._loop()
        if self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._sem = asyncio.Semaphore(self.concurrency)
            self._lock_loop = loop
        # 有冷却时必须串行：并发进入的多个任务都会各自读到 remaining()=0，
        # 冷却等于形同虚设（实测 3 个任务 0.00s 全部放行）。
        if self.concurrency <= 1 or self.cooldown_seconds > 0:
            return self._lock
        return self._sem

    def reset_primitive(self) -> None:
        """配置变更后重建并发原语；只在没有任务在跑时调用。"""
        self._lock = None
        self._sem = None
        self._lock_loop = None

    # ---------- 提交 ----------

    async def submit(
        self,
        run: Callable[[], Awaitable[Any]],
        *,
        on_wait: Callable[[float, int], None] | None = None,
    ) -> Any:
        if self._waiting + self._running >= self.max_queue:
            raise QueueFullError(
                f"{self.provider_id} 队列已满（{self.max_queue} 个任务在等待）"
            )
        self._waiting += 1
        position = self._waiting + self._running
        primitive = self._primitive()
        entered = False
        try:
            async with primitive:
                entered = True
                self._waiting -= 1
                self._running += 1
                wait = self.remaining()
                if wait > 0:
                    logger.info(
                        "[妙笔][queue] %s 冷却剩余 %.0fs，第 %d 位等待中",
                        self.provider_id,
                        wait,
                        position,
                    )
                    if on_wait:
                        try:
                            on_wait(wait, position)
                        except Exception:  # noqa: BLE001
                            pass
                    await asyncio.sleep(wait)
                try:
                    result = await run()
                except BaseException as exc:  # noqa: BLE001
                    if isinstance(exc, Exception) and _should_cooldown(exc):
                        self.mark()
                    raise
                else:
                    self.mark()
                    return result
        finally:
            if entered:
                self._running = max(0, self._running - 1)
            else:
                self._waiting = max(0, self._waiting - 1)

    # ---------- 观测 ----------

    def snapshot(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "running": self._running,
            "waiting": self._waiting,
            "concurrency": self.concurrency,
            "cooldown_seconds": self.cooldown_seconds,
            "cooldown_remaining": round(self.remaining(), 1),
            "max_queue": self.max_queue,
        }


class QueueManager:
    """按 provider_id 持有队列；配置变化时就地更新参数。"""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self._queues: dict[str, ProviderQueue] = {}

    def queue_for(self, provider: dict[str, Any]) -> ProviderQueue:
        pid = str(provider.get("id") or "")
        queue = self._queues.get(pid)
        if queue is None:
            queue = ProviderQueue(
                pid,
                concurrency=provider.get("concurrency") or 1,
                cooldown_seconds=provider.get("cooldown_seconds") or 0,
                max_queue=provider.get("max_queue") or 8,
                state_path=self.state_dir / f"{pid}.json",
            )
            self._queues[pid] = queue
        else:
            concurrency = max(1, int(provider.get("concurrency") or 1))
            changed = concurrency != queue.concurrency
            queue.concurrency = concurrency
            queue.cooldown_seconds = max(0.0, float(provider.get("cooldown_seconds") or 0))
            queue.max_queue = max(1, int(provider.get("max_queue") or 8))
            # 信号量容量在创建时就固定了，改并发必须重建；仅在完全空闲时做，避免破坏互斥
            if changed and queue._running == 0 and queue._waiting == 0:
                queue.reset_primitive()
        return queue

    def snapshots(self) -> list[dict[str, Any]]:
        return [queue.snapshot() for queue in self._queues.values()]
