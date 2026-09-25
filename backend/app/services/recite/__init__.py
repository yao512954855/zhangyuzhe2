"""语文背诵引擎 —— WP-BE-05（需求 §5.6/§5.7/§6.3/§6.4，契约 §7.3）。

对外入口：

```python
from app.services.recite import ReciteEngine, ReciteService
```

- `ReciteEngine`：纯内存对齐引擎（无 IO），按 `recitation_id` 缓存。
- `ReciteService`：HTTP 编排 —— 裁剪范围、落库、结算重算防刷分。

子模块：

| 模块 | 职责 |
|---|---|
| `types` | `FeedResult` / `ReciteResult`（契约 §7.3 冻结）+ 内部 `Alignment` |
| `aligner` | **拼音层**序列对齐（窗口内局部最优 + 编辑距离回溯） |
| `engine` | 指针推进、噪声判定、卡壳判定、三档提示、结算 |
| `scoring` | 三部分加权评分与评级 |
| `service` | 创建/喂入/提示/补报/结算 + `recitations` 与 `recitation_events` 落库 |

⚠️ 导入顺序有意为之（types → aligner → engine → service），
避免包内循环导入。
"""

from app.services.recite.types import FeedResult, ReciteResult
from app.services.recite.aligner import (
    align_sequences,
    best_alignment,
    compile_target,
    match_ratio,
    to_pinyin_seq,
)
from app.services.recite.engine import ReciteEngine
from app.services.recite.scoring import compute_score, grade_of, hint_total
from app.services.recite.service import ReciteService

__all__ = [
    "FeedResult",
    "ReciteResult",
    "ReciteEngine",
    "ReciteService",
    "align_sequences",
    "best_alignment",
    "compile_target",
    "match_ratio",
    "to_pinyin_seq",
    "compute_score",
    "grade_of",
    "hint_total",
]
