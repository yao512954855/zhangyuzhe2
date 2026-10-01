"""判分引擎包 —— WP-BE-04 / 契约 §7.1。

| 模块 | 职责 |
|---|---|
| `types.py` | `Verdict` / `DiffItem`（**契约冻结，不得改字段**） |
| `normalize.py` | 两套归一化：文本（保空格）/ 拼读（只留字母） |
| `homophone.py` | 谐音映射表加载与匹配（数据在 `app/data/homophone_map.yaml`） |
| `text_judge.py` | 英语说词 / 英译中（需求 §6.1） |
| `spell_judge.py` | 拼读 + 逐位对齐（需求 §6.2） |
| `service.py` | `JudgeService` 门面（对外唯一入口） |
| `word_spell.py` | 英语模式一「双口径」判定（整词 → 字母序列回退，二期口径） |

**对外只用 `JudgeService`**，不要直接 import 内部模块 ——
BE-06 / 管理端申诉复核都通过它调用，便于统一替换实现。

⚠️ 唯一例外是 `judge_word_or_spell`：它是**两个链路共用的组合口径**
（REST `submit_answer` 与 WS `_judge_and_emit` 都要用），
放在这里是为了避免两处各写一遍。它内部仍然只调 `JudgeService`。
"""

from app.services.judge.service import JudgeService
from app.services.judge.types import DiffItem, MatchType, Verdict
from app.services.judge.word_spell import judge_word_or_spell

__all__ = [
    "JudgeService",
    "Verdict",
    "DiffItem",
    "MatchType",
    "judge_word_or_spell",
]
