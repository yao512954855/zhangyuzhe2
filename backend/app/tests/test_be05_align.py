"""WP-BE-05 拼音层对齐 —— 验收测试（10b U1 ~ U6 + 边界）。

对齐算法是纯计算、不碰数据库；库相关的验收（结算、事件落库、防刷分）
在 `test_be05_engine.py`。

覆盖要求（10b DoD）：**对齐的四种状态都有对应用例** ——
`match` / `wrong` / `missing` / `extra` 各至少一条，且位置与内容都断言到。

### 两个必须钉住的算法特性

**① 认领边界（`claimed_end`）**：对齐片段故意比识别文本长一截，
好识别「学生跳过几个字继续往下背」；但片段尾部那串没人认领的目标位
必须被排除在推进与漏字统计之外，否则「只念了前 4 个字」会把后面 3 个字判成漏字（U1）。

**② 同音字必须过**（需求 §6.3）：的/地/得、在/再、他/她/它 在拼音层是同一个音，
汉字层比对会全判错。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be05a_"))
os.environ.setdefault("DB_PATH", str(_TMP_DIR / "align.db"))
os.environ.setdefault("BACKUP_DIR", str(_TMP_DIR / "backups"))
os.environ.setdefault("JWT_SECRET", "be05-align-secret-0123456789abcd")

import pytest  # noqa: E402

from app.services.recite import ReciteEngine  # noqa: E402
from app.services.recite.aligner import (  # noqa: E402
    align_sequences,
    best_alignment,
    candidate_starts,
    compile_target,
    match_ratio,
    split_pinyin,
    to_pinyin_seq,
)


def engine_of(text: str, **kwargs) -> ReciteEngine:
    """按课文文本造一个引擎（拼音现场生成，等于导入期的产物）。"""
    return ReciteEngine(
        content_plain=text,
        content_pinyin=" ".join(to_pinyin_seq(text)),
        scope=[0],
        **kwargs,
    )


# ===========================================================================
# U1 ~ U6：10b 规格的六条对齐验收
# ===========================================================================


class TestSpecCases:
    def test_u1_first_segment(self):
        """U1｜目标「我与父亲不相见」，喂入「我与父亲」→ pos 推进到 4，点亮 0..3。"""
        engine = engine_of("我与父亲不相见")
        result = engine.feed("我与父亲")

        assert result.pos == 4
        assert result.newly_matched == [0, 1, 2, 3]
        assert result.is_noise is False
        # 关键：后面 3 个字是「还没背到」，不是「漏了」
        assert engine._missed == set()

    def test_u2_incremental_only(self):
        """U2｜再喂入累计文本「我与父亲不相见」→ 只消费新增，无重复匹配。"""
        engine = engine_of("我与父亲不相见")
        engine.feed("我与父亲")
        result = engine.feed("我与父亲不相见")

        assert result.pos == 7
        assert result.newly_matched == [4, 5, 6]
        assert engine.matched_count == 7

    @pytest.mark.parametrize(
        ("expected", "said"),
        [
            ("他的背影", "她的背影"),          # 他 / 她
            ("慢慢地走", "漫漫地走"),          # 慢 / 漫
            ("我在家里", "我再家里"),          # 在 / 再
            ("快乐地跑", "快乐得跑"),          # 地 / 得
        ],
    )
    def test_u3_homophones_pass(self, expected: str, said: str):
        """U3｜同音字：拼音相同 → 全部匹配，**不记错**（需求 §6.3）。"""
        engine = engine_of(expected)
        engine.feed(said)

        assert engine.matched_count == len(expected)
        assert engine._missed == set()

    def test_u4_missing_two_chars(self):
        """U4｜漏字：跳过「他的」→ 记入 missed，pos 继续推进到末尾。

        这条钉子最要紧：对齐片段必须够长，「背影」才能被认领；
        否则指针会停在 10，学生明明背完了却显示还剩 2 个字。
        """
        engine = engine_of("我最不能忘记的是他的背影")
        engine.feed("我最不能忘记的是背影")

        assert sorted(engine._missed) == [8, 9]       # 「他」「的」
        assert engine.pos == 12                        # 一路推到末尾
        assert engine.matched_count == 10

    def test_u5_reread_does_not_rewind(self):
        """U5｜回读：重复已背内容 → pos 不回退、不判错。"""
        engine = engine_of("我与父亲")
        result = engine.feed("我与父亲我与父亲")

        assert engine.pos == 4
        assert engine._missed == set()
        assert engine.matched_count == 4
        assert result.is_noise is False

    def test_u6_noise_ignored(self):
        """U6｜噪声：完全无关的乱语 → is_noise，pos 不推进、不记错。"""
        engine = engine_of("我与父亲不相见")
        result = engine.feed("今天天气真不错")

        assert result.is_noise is True
        assert result.newly_matched == []
        assert engine.pos == 0
        assert engine._missed == set()


# ===========================================================================
# 四种对齐状态都要能构造出来（DoD 要求）
# ===========================================================================


class TestFourStates:
    def test_match_and_wrong(self):
        """等长比对：位置对得上但音不同 → `wrong`（念错）。"""
        steps = align_sequences(["wo", "yu"], ["wo", "ni"])
        assert [s.state for s in steps] == ["match", "wrong"]

    def test_missing(self):
        """目标有、识别没有 → `missing`。"""
        steps = align_sequences(["wo", "yu", "fu"], ["wo", "fu"])
        assert "missing" in [s.state for s in steps]
        assert len(steps) == 3

    def test_extra(self):
        """识别多出来的 → `extra`，且不占用目标位。"""
        steps = align_sequences(["wo", "yu"], ["wo", "yu", "ne"])
        assert [s.state for s in steps] == ["match", "match", "extra"]
        assert steps[-1].ti is None

    def test_empty_sides(self):
        assert align_sequences([], []) == []
        assert [s.state for s in align_sequences([], ["a"])] == ["extra"]
        assert [s.state for s in align_sequences(["a"], [])] == ["missing"]


# ===========================================================================
# 认领边界 —— 算法最容易写错的地方
# ===========================================================================


class TestClaimedBoundary:
    def test_trailing_missing_not_counted(self):
        """尾部未认领的 missing 既不算点亮、也不算漏字。"""
        engine = engine_of("一二三四五六七八九十")
        engine.feed("一二三四五")

        assert engine.pos == 5
        assert engine._missed == set()

    def test_missing_in_the_middle_is_counted(self):
        """中间被认领的 missing 才算漏字（后面还有内容接上）。"""
        engine = engine_of("一二三四五六七八九十")
        engine.feed("一二三六七八九十")

        assert sorted(engine._missed) == [3, 4]        # 四、五
        assert engine.pos == 10
        assert engine.matched_count == 8

    def test_claimed_end_excludes_trailing_missing(self):
        target = ["a", "b", "c", "d", "e"]
        actual = ["a", "b"]
        alignment = best_alignment(target, actual, 0, 5)

        assert alignment is not None
        assert alignment.claimed_end == 2
        assert alignment.matched == [0, 1]
        assert alignment.missed == []

    def test_distance_counts_full_fragment(self):
        """`distance` 仍按完整片段算（含尾部未认领），否则不同起点不可比。"""
        alignment = best_alignment(["a", "b", "c", "d"], ["a", "b"], 0, 5)
        assert alignment is not None
        assert alignment.distance == 2          # c、d 未认领但仍计入距离


# ===========================================================================
# 边界与防御
# ===========================================================================


class TestEdgeCases:
    def test_empty_actual(self):
        assert best_alignment(["a"], [], 0, 5) is None
        assert match_ratio(None, 0) == 0.0

    def test_punctuation_and_digits_ignored(self):
        """标点/数字/字母不进对齐序列（需求 §5.6「标点不显示、不影响对齐」）。"""
        assert to_pinyin_seq("你好，世界！123 abc") == to_pinyin_seq("你好世界")

    def test_empty_engine(self):
        engine = ReciteEngine(content_plain="", content_pinyin="", scope=[])
        assert engine.total_chars == 0
        result = engine.feed("随便说点")
        assert result.is_noise is True
        assert engine.pos == 0

    def test_compile_target_truncates_mismatch(self):
        """`plain` 与 `pinyin` 不等长时按较短者截断（防御脏数据，不抛异常）。"""
        chars, pinyin = compile_target("你好世界", "ni hao")
        assert len(chars) == len(pinyin) == 2

    def test_split_pinyin(self):
        assert split_pinyin("wo yu fu qin") == ["wo", "yu", "fu", "qin"]
        assert split_pinyin("") == []

    def test_candidate_starts_falls_back_to_full_scan(self):
        """首 token 对不上时退化为全窗口枚举（否则开头就说错会直接判噪声）。"""
        starts = candidate_starts(["a", "b", "c"], ["z"], 0, 3)
        assert starts == [0, 1, 2, 3]

    def test_candidate_starts_prefers_first_token_match(self):
        starts = candidate_starts(["a", "b", "a", "c"], ["a"], 0, 3)
        assert starts == [0, 2]

    def test_long_actual_is_truncated(self):
        """超长识别结果截断，避免 DP 爆掉（防御，不是业务语义）。"""
        engine = engine_of("你好世界")
        engine.feed("你好世界" * 300)
        assert engine.pos <= engine.total_chars

    def test_pinyin_seq_is_stable(self):
        """同文本两次转换结果必须一致 —— 否则导入期与背诵期会对齐不上。"""
        text = "背影是朱自清的名篇"
        assert to_pinyin_seq(text) == to_pinyin_seq(text)
