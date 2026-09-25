"""判分引擎包 —— WP-BE-04 / 契约 §7.1。

| 模块 | 职责 |
|---|---|
| `types.py` | `Verdict` / `DiffItem`（**契约冻结，不得改字段**） |
| `normalize.py` | 两套归一化：文本（保空格）/ 拼读（只留字母） |
| `homophone.py` | 谐音映射表加载与匹配（数据在 `app/data/homophone_map.yaml`） |
| `text_judge.py` | 英语说词 / 英译中（需求 §6.1） |
| `spell_judge.py` | 拼读 + 逐位对齐（需求 §6.2） |
| `service.py` | `JudgeService` 门面（对外唯一入口） |

**对外只用 `JudgeService`**，不要直接 import 内部模块 ——
BE-06 / 管理端申诉复核都通过它调用，便于统一替换实现。
"""

from app.services.judge.service import JudgeService
from app.services.judge.types import DiffItem, MatchType, Verdict

__all__ = ["JudgeService", "Verdict", "DiffItem", "MatchType"]
