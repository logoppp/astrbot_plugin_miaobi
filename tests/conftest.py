"""把插件根目录挂进 sys.path，让测试能用 `from core.xxx import ...`。

容器内跑：`python3 -m pytest /AstrBot/data/plugins/astrbot_plugin_miaobi/tests -q`
容器外跑：`core.registry` / `core.service` 依赖 `astrbot.api`，那两项会被 skip。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
