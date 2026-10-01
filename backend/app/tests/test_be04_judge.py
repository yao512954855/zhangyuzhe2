"""WP-BE-04 判分引擎 —— 验收测试（10a U1 ~ U15 + 边界）。

**纯函数测试，不依赖数据库**：判分引擎是纯计算，不该为测它去起库。
库相关的验收（掌握度、组卷、错题本、申诉）在 `test_be04_session.py`。

覆盖要求（10a DoD）：**`diff` 回显的四种状态都有对应用例** ——
`match` / `wrong` / `missing` / `extra` 各至少一条，且位置与内容都断言到。
"""

from __future__ import annotations

import pytest

from app.services.judge import JudgeService, judge_word_or_spell
from app.services.judge.normalize import (
    cn_number_to_arabic,
    is_empty_answer,
    normalize_letters,
    normalize_text,
)
from app.services.judge.spell_judge import align_diff, edit_distance


# ===========================================================================
# 归一化（需求 §6.1）
# ===========================================================================


class TestNormalizeText:
    @pytest.mark.parametrize(
        ("raw", "expected_norm"),
        [
            ("  ClassRoom ", "classroom"),
            ("C L A S S", "c l a s s"),          # 空格分隔的字母序列，归一化保留空格
            ("c-l-a-s-s", "c l a s s"),           # 连字符变空格
            ("don't", "don't"),                   # 撇号保留
            ("Don’t stop!", "don't stop"),        # 弯引号撇号 + 标点剥除
            ("Hello,  World!!", "hello world"),   # 连续空白合一
            ("教室。", "教室"),                    # 中文标点剥离
            ("我 与 父亲", "我与父亲"),            # 汉字之间不留空格
            ("３", "3"),                          # 全角数字转半角
            ("三", "3"),                          # 中文数字转阿拉伯
            ("十一", "11"),
            ("一百零五", "105"),
            ("二零二六", "二零二六"),              # 年份这类无法确定语义 → 保持原样
            ("两三", "两三"),                      # 并列列举 → 不猜
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalize(self, raw, expected_norm):
        assert normalize_text(raw) == expected_norm

    def test_numbers_only(self):
        assert cn_number_to_arabic("十") == "10"
        assert cn_number_to_arabic("二十") == "20"
        assert cn_number_to_arabic("三十五") == "35"


class TestNormalizeLetters:
    @pytest.mark.parametrize(
        ("raw", "expected_seq"),
        [
            ("c-l-a-s-s", "class"),
            ("C L A S S", "class"),
            ("  class  ", "class"),
            ("c.l.a.s.s", "class"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_letters(self, raw, expected_seq):
        assert normalize_letters(raw) == expected_seq


class TestIsEmpty:
    @pytest.mark.parametrize(
        ("raw", "empty"),
        [(None, True), ("", True), ("   ", True), ("。", True), ("!!!", True),
         ("class", False), ("教室", False), ("3", False)],
    )
    def test_empty(self, raw, empty):
        assert is_empty_answer(raw) is empty


# ===========================================================================
# 文本判定（需求 §6.1，U1 ~ U8）
# ===========================================================================


class TestJudgeText:
    def test_u1_exact(self):
        """U1：完全一致。"""
        verdict = JudgeService.judge_text(answer="classroom", expected="classroom")
        assert verdict.match_type == "exact"
        assert verdict.is_correct is True
        assert verdict.can_appeal is False

    def test_u2_normalized_exact(self):
        """U2：大小写 + 首尾空格，归一化后 exact。"""
        verdict = JudgeService.judge_text(answer="ClassRoom ", expected="classroom")
        assert verdict.match_type == "exact"
        assert verdict.normalized == "classroom"

    def test_u3_accepted(self):
        """U3：命中可接受答案。"""
        verdict = JudgeService.judge_text(answer="课室", expected="教室", accepted=["课室"])
        assert verdict.match_type == "exact"
        assert verdict.is_correct is True

    def test_u4_loose(self):
        """U4：包含匹配，长度 ≤ 2 倍 → loose 且可申诉。"""
        verdict = JudgeService.judge_text(answer="a classroom", expected="classroom")
        assert verdict.match_type == "loose"
        assert verdict.is_correct is True
        assert verdict.can_appeal is True

    def test_u4_loose_rejects_over_length(self):
        """边界：包含但长度超过 2 倍 → 不判 loose。"""
        verdict = JudgeService.judge_text(
            answer="a very long classroom sentence here", expected="classroom"
        )
        assert verdict.match_type != "loose"

    def test_u5_fuzzy(self):
        """U5：相似度达标 → fuzzy 且可申诉。"""
        verdict = JudgeService.judge_text(answer="restauran", expected="restaurant")
        assert verdict.match_type == "fuzzy"
        assert verdict.is_correct is True
        assert verdict.can_appeal is True

    def test_u6_short_word_not_fuzzy(self):
        """U6：目标长度 3，即便差 1 位也不走 fuzzy。"""
        verdict = JudgeService.judge_text(answer="car", expected="cat")
        assert verdict.match_type == "wrong"
        assert verdict.is_correct is False

    def test_u7_homophone_not_equal(self):
        """U7：同音词不自动等同（需求 §6.1 明文）。"""
        verdict = JudgeService.judge_text(answer="their", expected="there")
        assert verdict.match_type == "wrong"

    def test_u7_homophone_with_accepted(self):
        """同音词只有显式配了 accepted 才算对。"""
        verdict = JudgeService.judge_text(answer="their", expected="there", accepted=["their"])
        assert verdict.match_type == "exact"

    def test_u8_empty(self):
        """U8：识别为空 → empty，不计错。"""
        for raw in (None, "", "   "):
            verdict = JudgeService.judge_text(answer=raw, expected="class")
            assert verdict.match_type == "empty"
            assert verdict.is_correct is False
            assert verdict.can_appeal is False
            assert verdict.counts_as_error is False

    def test_chinese_translation(self):
        """英译中：中文答案同样走五级顺序。"""
        verdict = JudgeService.judge_text(answer="教室。", expected="教室")
        assert verdict.match_type == "exact"

    def test_confidence_passthrough(self):
        verdict = JudgeService.judge_text(
            answer="classroom", expected="classroom", asr_confidence=0.94
        )
        assert verdict.asr_confidence == 0.94

    def test_text_verdict_has_no_diff(self):
        """文本判定不该产出 diff（只有拼读有）。"""
        verdict = JudgeService.judge_text(answer="classroom", expected="classroom")
        assert verdict.diff is None


# ===========================================================================
# 拼读判定（需求 §6.2，U9 ~ U15）
# ===========================================================================


class TestJudgeSpell:
    def test_u9_exact_all_match(self):
        """U9：完全一致，diff 全 match。"""
        verdict = JudgeService.judge_spell(asr_text="class", target="class")
        assert verdict.match_type == "exact"
        assert [d.state for d in verdict.diff] == ["match"] * 5

    def test_u10_wrong_at_position_2(self):
        """U10：第 3 位听错 → `diff[2] = {pos:2, expected:"a", actual:"e", state:"wrong"}`。"""
        verdict = JudgeService.judge_spell(asr_text="c l e s s", target="class")
        assert verdict.match_type == "wrong"
        assert len(verdict.diff) == 5
        item = verdict.diff[2]
        assert (item.pos, item.expected, item.actual, item.state) == (2, "a", "e", "wrong")

    def test_u11_missing(self):
        """U11：末位没听到 → `diff[4].state='missing'`，长度仍为 5。"""
        verdict = JudgeService.judge_spell(asr_text="c l a s", target="class")
        assert len(verdict.diff) == 5
        assert verdict.diff[4].state == "missing"
        assert verdict.diff[4].expected == "s"
        assert verdict.diff[4].actual is None

    def test_u12_extra(self):
        """U12：多念一个 → 末位 `extra`，长度为 6（max(len)）。"""
        verdict = JudgeService.judge_spell(asr_text="c l a s s s", target="class")
        assert len(verdict.diff) == 6
        assert verdict.diff[-1].state == "extra"
        assert verdict.diff[-1].expected is None
        assert verdict.diff[-1].actual == "s"

    def test_u13_english_homophone(self):
        """U13：英文读音谐音 → 映射回字母序列。"""
        verdict = JudgeService.judge_spell(asr_text="see ell aye ess ess", target="class")
        assert verdict.match_type == "exact"
        assert verdict.normalized == "class"

    def test_u14_chinese_homophone(self):
        """U14：中文谐音命中映射表。"""
        verdict = JudgeService.judge_spell(asr_text="克拉斯", target="class")
        assert verdict.match_type == "exact"
        assert verdict.normalized == "class"

    def test_u15_fuzzy_one_diff(self):
        """U15：目标长度 ≥6 且仅差 1 位 → fuzzy，可申诉。"""
        verdict = JudgeService.judge_spell(asr_text="classroom", target="classrom")
        assert verdict.match_type == "fuzzy"
        assert verdict.can_appeal is True

    def test_fuzzy_requires_length_6(self):
        """边界：目标长度 5 差 1 位 → 不走 fuzzy（门槛是 ≥6，比文本判定严）。"""
        verdict = JudgeService.judge_spell(asr_text="class", target="clas")
        assert verdict.match_type == "wrong"

    def test_multi_letter_token(self):
        """`double u` 这类多词组合也要能还原。"""
        verdict = JudgeService.judge_spell(asr_text="double u a t e r", target="water")
        assert verdict.match_type == "exact"

    def test_r_reading_not_split(self):
        """ASR 把 R 的读音识别成 `are` 时，不能被拆成 `ar` + `e`（凭空多一个字母）。

        这是逐字符最长匹配 + 谐音表歧义的经典坑：`ar` 是 R 的合法读音，
        于是 `are` 的前两位被吃掉、`e` 又单独成字母，结果 `classroom` → `classreoom`。
        """
        verdict = JudgeService.judge_spell(
            asr_text="see ell aye ess ess are oh oh em", target="classroom"
        )
        assert verdict.normalized == "classroom"
        assert verdict.match_type == "exact"

    def test_whole_word_returned_by_asr(self):
        """F7：ASR 直接返回整个单词也要能判对（连读念法）。"""
        verdict = JudgeService.judge_spell(asr_text="classroom", target="classroom")
        assert verdict.match_type == "exact"

    def test_empty_spell(self):
        """识别为空 → empty，不计错，且不判 diff 对错。"""
        for raw in (None, "", "   ", "。"):
            verdict = JudgeService.judge_spell(asr_text=raw, target="class")
            assert verdict.match_type == "empty"
            assert verdict.is_correct is False

    def test_diff_always_present_when_not_empty(self):
        """回显是硬需求：非 empty 一定给 diff（无论对错）。"""
        for raw in ("class", "cless", "clas", "classs"):
            verdict = JudgeService.judge_spell(asr_text=raw, target="class")
            assert verdict.diff, f"{raw} 没有产 diff"
            assert all(d.state in ("match", "wrong", "missing", "extra") for d in verdict.diff)

    def test_normalized_used_for_letter_seq(self):
        """`normalized` 即 `answers.letter_seq` 的入库值。"""
        verdict = JudgeService.judge_spell(asr_text="c-l-e-s-s", target="class")
        assert verdict.normalized == "cless"


# ===========================================================================
# 对齐算法本身（四种状态的独立用例）
# ===========================================================================


class TestAlignDiff:
    @pytest.mark.parametrize(
        ("target", "actual", "states"),
        [
            ("class", "class", ["match"] * 5),
            ("class", "cless", ["match", "match", "wrong", "match", "match"]),
            ("class", "clas", ["match", "match", "match", "match", "missing"]),
            ("class", "classs", ["match"] * 5 + ["extra"]),
        ],
    )
    def test_states(self, target, actual, states):
        assert [d.state for d in align_diff(target, actual)] == states

    def test_extra_merges_transposition(self):
        """乱序（`ab` vs `ba`）不能产出超过 `max(len)` 长度的 diff。"""
        items = align_diff("ab", "ba")
        assert len(items) <= max(2, 2)
        assert all(item.pos == index for index, item in enumerate(items))

    def test_empty_actual_all_missing(self):
        items = align_diff("cat", "")
        assert [d.state for d in items] == ["missing"] * 3

    def test_empty_target_all_extra(self):
        items = align_diff("", "cat")
        assert [d.state for d in items] == ["extra"] * 3

    def test_pos_is_sequential(self):
        for target, actual in (("class", "cless"), ("class", "clas"), ("class", "classs")):
            items = align_diff(target, actual)
            assert [d.pos for d in items] == list(range(len(items)))


class TestEditDistance:
    @pytest.mark.parametrize(
        ("left", "right", "distance"),
        [
            ("class", "class", 0),
            ("class", "cless", 1),
            ("class", "clas", 1),
            ("class", "classs", 1),
            ("class", "c", 4),
            ("", "abc", 3),
            ("abc", "", 3),
        ],
    )
    def test_distance(self, left, right, distance):
        assert edit_distance(left, right) == distance


class TestJudgeWordOrSpell:
    """英语模式一「双口径」判定（2026-09-29 姚工拍板）。

    口径：念整词 / 逐字母拼**都算对**，一个词只出一条判分。
    ⚠️ 忠实度优先 —— 正样本（念字母也能判对）**不足以**说明口径没被放宽，
    所以这里必须同时有「读错 / 乱说仍判错」的负样本。
    """

    # ---- 正样本：两条口径都能判对 ----

    def test_whole_word_exact(self):
        verdict, used_spell = judge_word_or_spell(answer="apple", expected="apple")
        assert verdict.is_correct is True
        assert verdict.match_type == "exact"
        assert used_spell is False, "念整词走文本口径，不该标成拼读"

    def test_spelled_letters_fallback(self):
        verdict, used_spell = judge_word_or_spell(answer="a p p l e", expected="apple")
        assert verdict.is_correct is True
        assert verdict.match_type == "exact"
        assert used_spell is True, "字母序列回退命中，调用方据此落 letter_seq"

    def test_hyphenated_letters_fallback(self):
        verdict, used_spell = judge_word_or_spell(answer="a-p-p-l-e", expected="apple")
        assert verdict.is_correct is True
        assert used_spell is True

    def test_phonetic_letters_fallback(self):
        # 云 ASR 对「逐字母念」的典型输出
        verdict, used_spell = judge_word_or_spell(
            answer="see ell aye ess ess", expected="class"
        )
        assert verdict.is_correct is True
        assert used_spell is True

    def test_accepted_answer_still_honoured(self):
        verdict, used_spell = judge_word_or_spell(
            answer="课室", expected="教室", accepted=["课室"]
        )
        assert verdict.is_correct is True
        assert used_spell is False

    # ---- 负样本：忠实度（读错 / 乱说 / 换了词都不能判对）----

    @pytest.mark.parametrize(
        "answer",
        [
            "appel",        # 字母顺序错
            "banana",       # 完全是别的词
            "orange",
            "apple apple",  # 连读两遍（口径未定，见 dev/02 §八 #96）
            "cat",
        ],
    )
    def test_wrong_stays_wrong(self, answer):
        verdict, used_spell = judge_word_or_spell(answer=answer, expected="apple")
        assert verdict.is_correct is False, f"{answer!r} 不该判对（忠实度红线）"
        assert verdict.match_type == "wrong"
        assert used_spell is False

    def test_empty_never_falls_back(self):
        for answer in (None, "", "   ", "，。"):
            verdict, used_spell = judge_word_or_spell(answer=answer, expected="apple")
            assert verdict.match_type == "empty"
            assert verdict.is_correct is False
            assert used_spell is False, "没出声不该靠谐音表碰对"

    def test_non_english_target_not_fallback(self):
        # 防御：目标不是英文（归一化后无 a-z）时拼读毫无意义 → 只走文本口径
        verdict, used_spell = judge_word_or_spell(answer="教室", expected="教室")
        assert verdict.is_correct is True
        assert used_spell is False
