"""拼音层对齐算法 —— WP-BE-05 / 需求 §6.3。

**为什么在拼音层对齐**（需求 §6.3 明文）：汉字识别结果必然带同音字错误
（的/地/得、在/再、他/她/它）。若在汉字层比对，这些会被全算成「背错」，
学生明明背对了却掉分。在拼音层比对，同音字自然通过，只有**真正的读音错**才算错。

**为什么不用 `judge/spell_judge.align_diff`**：那个函数按**单个字符**做编辑距离，
是为「拼字母」场景写的。拼音是**多字符 token**（`zhong` / `guo`），
按字符比会把 `zhang` vs `zang` 算成 2 处差异（而不是 1 处替换）。
本模块是**序列级**版本，元素是完整的拼音 token。

对齐状态语义（与 `spell_judge` 保持一致，便于全项目统一理解）：

| 状态 | 含义 | 背诵场景的处理 |
|---|---|---|
| `match` | 拼音相同 | 点亮该字 |
| `wrong` | 读音不同 | 该字**未点亮**，计入漏字 |
| `missing` | 学生跳过了这个字 | 该字**未点亮**，计入漏字 |
| `extra` | 学生多念了内容 | 忽略，不推进指针 |

> `wrong` 与 `missing` 合并计入「漏字」是有意为之：屏幕上两者的效果完全一样
> （那个字没亮），且需求 §6.4 只定义了「漏字数」一个指标、
> 正确率公式 = `1 − 漏字数 / 总字数`。若把 `wrong` 排除在外，
> 学生把整句读错反而不会掉分，与教学直觉相悖。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Sequence

from pypinyin import Style, pinyin

from app.core.logging import get_logger
from app.services.pinyin_service import char_pinyin, han_only
from app.services.recite.types import AlignStep, Alignment

logger = get_logger(__name__)

# 一次 `feed` 里候选起点的上限（防御：避免 ASR 吐超长文本时 DP 爆掉）
_MAX_ACTUAL_LEN = 400


# ---------------------------------------------------------------------------
# 文本 → 拼音序列
# ---------------------------------------------------------------------------


def to_pinyin_seq(text: str) -> list[str]:
    """任意文本 → 拼音 token 列表（只取汉字，标点/数字/字母直接丢弃）。

    与 `pinyin_service.build()` 同口径，保证**导入期生成的 `content_pinyin`
    与背诵期现场转换的结果一致**（否则会对齐不上）。
    """
    if not text:
        return []
    return [char_pinyin(ch) for ch in han_only(text)]


def split_pinyin(content_pinyin: str) -> list[str]:
    """`content_pinyin`（空格分隔）→ token 列表。"""
    if not content_pinyin:
        return []
    return str(content_pinyin).split()


# ---------------------------------------------------------------------------
# 多音字：让「地」既接受 dì 也接受 de
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4096)
def readings(ch: str) -> frozenset[str]:
    """单个汉字在 pypinyin 里的**全部**读音（无声调）。

    ### 为什么必须做这一步

    需求 §6.3 明说「对齐在拼音层进行，这样『的/地/得』『在/再』等同音字
    不会误判为错误」。但 **`lazy_pinyin` 只给一个默认读音**，而默认读音
    经常不是课文里的那一个：

        lazy_pinyin("地")               → ['di']       （默认 dì）
        pinyin("地", heteronym=True)     → [['di', 'de']]（作状语读 de）

    后果很硬：导入期 `content_pinyin` 把「快乐地跑」写成了 `kuai le di pao`，
    学生按标准读音念 `kuai le de pao` → 第 3 位对不上 → **判成背错**。
    这类字在小学课文里密度极高（地/得/的、了/着、长/重、行/乐），
    不修就等于把语文背诵的准确率打掉一大截。

    ⚠️ 这**只放宽对齐时的比较规则**，不动 `content_pinyin` 的生成方式 ——
    契约 §4.5 要求 `content_plain` / `content_pinyin` 严格等长且逐字生成，
    那条约束原样保留（候选集合里会**并上**库里实际存的那个 token，
    见 `build_candidates`，所以「照库里的拼音念」永远算对）。
    """
    if not ch:
        return frozenset()
    try:
        groups = pinyin(ch, style=Style.NORMAL, heteronym=True)
    except Exception:  # pragma: no cover - pypinyin 内部异常不该拖垮背诵
        logger.warning("取多音字读音失败：%r", ch)
        return frozenset()
    out: set[str] = set()
    for group in groups or []:
        for item in group or []:
            if item:
                out.add(str(item))
    return frozenset(out)


def build_candidates(chars: Sequence[str], tokens: Sequence[str]) -> list[frozenset[str]]:
    """构造「目标位 → 可接受读音集合」。

    集合 = 该字全部读音 ∪ 导入期实际写入 `content_pinyin` 的那个 token。
    后一项是保险：万一 pypinyin 版本升级导致读音表变化，
    库里存的值与现场算的 `readings` 不一致时，仍能保证照库念算对。
    """
    out: list[frozenset[str]] = []
    for index, ch in enumerate(chars):
        token = tokens[index] if index < len(tokens) else ""
        allowed = set(readings(ch))
        if token:
            allowed.add(str(token))
        out.append(frozenset(allowed))
    return out


def _token_match(expected: Any, actual: Any) -> bool:
    """单个 token 是否匹配（`expected` 允许是 `frozenset` 候选集合）。"""
    if isinstance(expected, (set, frozenset)):
        return actual in expected
    return expected == actual


def compile_target(
    content_plain: str, content_pinyin: str
) -> tuple[list[str], list[str]]:
    """把入库的 `content_plain` / `content_pinyin` 编译成对齐用的两个序列。

    契约 §4.5 硬约束：两者**严格等长**（导入期已校验）。
    这里仍做一次防御性检查 —— 线上脏数据不该让背诵功能 500，
    长度不一致时按较短者截断并告警（宁可少背几个字，也好过整块挂掉）。
    """
    chars = list(han_only(content_plain))
    pinyin = split_pinyin(content_pinyin)

    if len(chars) != len(pinyin):
        logger.warning(
            "content_plain(%d) 与 content_pinyin(%d) 长度不一致，按较短者截断",
            len(chars),
            len(pinyin),
        )
        keep = min(len(chars), len(pinyin))
        chars = chars[:keep]
        pinyin = pinyin[:keep]
    return chars, pinyin


# ---------------------------------------------------------------------------
# 序列级编辑距离对齐
# ---------------------------------------------------------------------------


def _dp_table(target: Sequence[str], actual: Sequence[str]) -> list[list[int]]:
    rows = len(target) + 1
    cols = len(actual) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        table[i][0] = i
    for j in range(cols):
        table[0][j] = j
    for i in range(1, rows):
        row = table[i]
        prev = table[i - 1]
        tc = target[i - 1]
        for j in range(1, cols):
            cost = 0 if _token_match(tc, actual[j - 1]) else 1
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + cost)
    return table


def align_sequences(target: Sequence[str], actual: Sequence[str]) -> list[AlignStep]:
    """序列级编辑距离回溯，产出逐位对齐。

    **回溯偏好 `extra` > `missing` > 对角**（与 `spell_judge` 同策略）。

    这个偏好顺序在背诵场景同样重要：学生「多加了一句」时，
    解说成「多念了这几个字」远比「把后面的字全读错了」贴近事实 ——
    后者会一次性污染几十个字的统计。
    """
    if not target and not actual:
        return []
    if not target:
        return [AlignStep(state="extra", ai=j) for j in range(len(actual))]
    if not actual:
        return [AlignStep(state="missing", ti=i) for i in range(len(target))]

    table = _dp_table(target, actual)
    i, j = len(target), len(actual)
    steps: list[AlignStep] = []

    while i > 0 or j > 0:
        if j > 0 and table[i][j] == table[i][j - 1] + 1:
            steps.append(AlignStep(state="extra", ai=j - 1))
            j -= 1
        elif i > 0 and table[i][j] == table[i - 1][j] + 1:
            steps.append(AlignStep(state="missing", ti=i - 1))
            i -= 1
        else:
            same = _token_match(target[i - 1], actual[j - 1])
            steps.append(
                AlignStep(
                    state="match" if same else "wrong",
                    ti=i - 1,
                    ai=j - 1,
                )
            )
            i -= 1
            j -= 1

    steps.reverse()
    return steps


def summarize(steps: list[AlignStep], start: int) -> Alignment:
    """把逐位对齐结果折算成 `Alignment`（相对下标 → 绝对下标）。

    分两步走（顺序不能反）：

    1. **先定认领边界** `claimed_end` —— 最后一个 `match`/`wrong` 之后的位置。
       尾部那些没被任何 `match`/`wrong` 认领的 `missing`，是「学生还没背到」，
       不参与任何统计（见 `types.Alignment` 的说明）。
    2. **再在认领区间内统计** `matched` / `missed`，**并同样只在认领区间内累计
       `distance`**。

    ### ⚠️ `distance` 为什么必须排除未认领尾部（2026-09-29 真机抓到的跳字缺陷）

    片段长度是 `len(actual) + allowance`，而片段**会在 `target` 末尾被截断**。
    于是「起点越靠后 → 片段越短 → 尾部待删的 `missing` 越少 → `distance` 越小」，
    起点之间**根本不可比**。后果是引擎被系统性地往**后**推：

        喂 '的背影'（pos=21）：
          start=21（真起点）片段 23 字 → 3 字全中，distance（含尾部 20 个 missing）= 20
          start=34（末尾的另一个「的」）片段 16 字 → 只中 1 字，distance = 15
        → 15 < 20，挑中 start=34，`pos` 从 21 直接跳到 37，**中间 13 个字全丢**。
        真机表现为「明明背完了，却只点亮 13/50、得分 32、评级待加强」。

    未认领的尾部在语义上**等于「学生还没背到这儿」**，不是错误，
    自然也不该参与「谁更优」的比较。排除后：start=21 → `distance=0`
    （认领区间 3 字全中）；start=34 → `distance=2`（认领区间只中 1 字、另 2 位念错）
    → `0 < 2`，正确回到 start=21。

    另注：`extra`（学生多念的内容）始终计入 `distance` —— 它落在 `actual` 侧，
    与起点无关，不存在不可比问题。
    """
    claimed_end = start
    for step in steps:
        if step.state == "extra":
            continue
        if step.state in ("match", "wrong"):
            claimed_end = max(claimed_end, start + int(step.ti) + 1)

    matched: list[int] = []
    missed: list[int] = []
    extra = 0
    distance = 0

    for step in steps:
        if step.state == "extra":
            extra += 1
            distance += 1
            continue

        index = start + int(step.ti)
        if index >= claimed_end:
            # 未认领：学生还没背到这儿，既不算点亮、不算漏字，也**不计入 distance**
            # （详见 docstring「为什么必须排除未认领尾部」）
            continue
        if step.state == "match":
            matched.append(index)
        else:
            missed.append(index)
            distance += 1

    return Alignment(
        start=start,
        end=start + sum(1 for s in steps if s.state != "extra"),
        matched=matched,
        missed=missed,
        extra=extra,
        distance=distance,
        steps=steps,
        claimed_end=claimed_end,
    )


# ---------------------------------------------------------------------------
# 窗口内局部最优
# ---------------------------------------------------------------------------


def candidate_starts(
    target: Sequence[str],
    actual: Sequence[str],
    pos: int,
    window: int,
) -> list[int]:
    """枚举窗口内的候选对齐起点。

    朴素做法是 `pos .. pos+window` 全枚举，每个起点跑一次 O(n·m) 的 DP。
    学生一口气背一段时 `m` 会到几百，全枚举就慢了。
    这里先按「首 token 相同」筛一遍 —— 学生从哪儿接着背，
    第一个音大概率是能对上的；筛完为空（比如开头就说错了）再退化为全枚举。
    """
    if not actual:
        return []
    end = min(pos + max(1, window), len(target))
    candidates = [
        s for s in range(pos, end + 1) if s < len(target) and _token_match(target[s], actual[0])
    ]
    if candidates:
        return candidates
    return list(range(pos, end + 1))


def best_alignment(
    target: Sequence[str],
    actual: Sequence[str],
    pos: int,
    window: int,
) -> Alignment | None:
    """在 `target[pos : pos+window]` 内找最佳对齐起点。

    择优标准（10b 规格原文：「取编辑距离最小、匹配字符数最多的位置」）：
    先比 **编辑距离** 升序，再比 **匹配字符数** 降序。

    ### 为什么片段要比 `actual` 长一截（`allowance`）

    朴素做法是拿 `target[s : s+len(actual)]` 去对齐 —— 两边等长，
    这会让**漏字场景彻底失效**：

        目标：我最不能忘记的是他的背影（12 字）
        学生：我最不能忘记的 + 背影      （跳过「他的」，10 字）

    等长切片只取到 `我最不能忘记的是他的`（前 10 字），
    「背影」两个字**根本没进对齐窗口**，于是 DP 只能把它解释成
    「把『他的背』念成了『的背影』」→ 指针停在 10，学生明明背完了却显示还剩 2 个字。

    所以片段长度取 `len(actual) + window`：允许「学生跳过一截、继续往下背」。
    多出的 `window` 个目标位若无人认领，自然变成 `missing`（漏字），
    而正因为 DP 会优先取编辑距离最小的解，「漏 2 个字然后接上」
    （距离 2）会打败「4 个字全念错」（距离 4），产出学生真正做了什么。

    Returns:
        最佳 `Alignment`；`actual` 为空、或窗口内无可对齐内容时返回 `None`。
    """
    if not actual:
        return None

    actual = list(actual[:_MAX_ACTUAL_LEN])
    allowance = max(0, int(window or 0))
    best: Alignment | None = None
    best_key: tuple[int, int] | None = None

    for start in candidate_starts(target, actual, pos, window):
        segment = list(target[start : start + len(actual) + allowance])
        steps = align_sequences(segment, actual)
        alignment = summarize(steps, start)
        key = (alignment.distance, -len(alignment.matched))
        if best_key is None or key < best_key:
            best_key = key
            best = alignment

    return best


def match_ratio(alignment: Alignment | None, actual_len: int) -> float:
    """匹配率 = 匹配字符数 / 识别字符数（需求 §6.3 的噪声判定输入）。"""
    if not alignment or actual_len <= 0:
        return 0.0
    return len(alignment.matched) / actual_len
