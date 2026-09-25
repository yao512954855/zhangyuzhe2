"""谐音映射 —— WP-BE-04 / 需求 §6.2 第 2 步。

把云 ASR 对「念字母」的识别结果还原成字母序列。三种典型输入：

| ASR 返回 | 目标 | 走哪一层 |
|---|---|---|
| `"see ell aye ess ess"` | `class` | `letters`（逐 token 查读音） |
| `"C L A S S"` | `class` | `letters`（单字母自身） |
| `"c-l-a-s-s"` | `class` | 无空格可切 → 逐字符 + 去非字母 |
| `"克拉斯"` | `class` | `words`（整串音译，无法逐字母还原） |
| `"double u"` | `w` | `tokens`（多词组合） |

**设计原则：不做模糊猜测。** 查不到就读作原字符，由 `normalize_letters` 的
「去非字母」自然丢弃。宁可漏，不可把错读判对 —— 与需求 §6.2 的
「判分模型必须忠实」是同一条原则。

配置文件：`app/data/homophone_map.yaml`（扩容纪律见该文件头部注释）。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

from app.core.excel import to_halfwidth
from app.core.logging import get_logger
from app.services.judge.normalize import normalize_letters

logger = get_logger(__name__)

# 逐字符扫描时，一个位置最多向前匹配几个字符（覆盖「艾克斯」这类 3 字读音）
_MAX_CJK_SPAN = 3

_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "homophone_map.yaml"


class HomophoneMap:
    """谐音映射表的加载与匹配。进程内单例（`get_map()`）。"""

    def __init__(
        self,
        *,
        letters: dict[str, list[str]],
        tokens: dict[str, str],
        words: dict[str, str],
        version: int = 1,
    ) -> None:
        self.version = version
        self.letters = letters
        self.tokens = {self._key(k): normalize_letters(v) for k, v in tokens.items()}
        self.words = {self._key(k): normalize_letters(v) for k, v in words.items()}
        self._reverse = self._build_reverse_index(letters)

    # ------------------------------------------------------------------ 构建

    @staticmethod
    def _key(text: str) -> str:
        """键的归一化：全角转半角 + 小写 + 空白合一（需求 §F6「加载时做归一化」）。"""
        return " ".join(to_halfwidth(str(text)).lower().split())

    @classmethod
    def from_file(cls, path: Path | str = _DATA_FILE) -> HomophoneMap:
        """从 yaml 加载。文件缺失或格式错误 → 降级为空表并**告警**，不阻断服务。

        谐音表缺失时拼读会退化成「只认直接念字母」，属于功能降级而非崩溃，
        因此不抛异常；但必须留下日志，否则线上会静默失效。
        """
        try:
            raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            logger.error("谐音映射表不存在：%s —— 拼读将退化为仅识别直接念字母", path)
            return cls(letters={}, tokens={}, words={})
        except yaml.YAMLError as exc:  # pragma: no cover - 配置写坏时才触发
            logger.error("谐音映射表解析失败：%s —— %s", path, exc)
            return cls(letters={}, tokens={}, words={})

        letters = {
            str(k).strip().lower(): [str(x) for x in (v or [])]
            for k, v in (raw.get("letters") or {}).items()
        }
        tokens = {str(k): str(v) for k, v in (raw.get("tokens") or {}).items()}
        words = {str(k): str(v) for k, v in (raw.get("words") or {}).items()}
        instance = cls(
            letters=letters,
            tokens=tokens,
            words=words,
            version=int(raw.get("version") or 1),
        )
        logger.debug(
            "谐音映射表已加载 v%s：%d 个字母 / %d 个组合 / %d 个整词",
            instance.version, len(instance.letters), len(instance.tokens), len(instance.words),
        )
        return instance

    @staticmethod
    def _build_reverse_index(letters: dict[str, list[str]]) -> dict[str, str]:
        """读音 → 字母（小写）反查表。

        同一读音映射到多个字母时**保留先出现的**并记 warning —— 字母按 A→Z
        书写，等于把优先级交给了配置文件的行序，可读且可调。
        """
        reverse: dict[str, str] = {}
        for letter, readings in letters.items():
            target = normalize_letters(letter)
            if len(target) != 1:
                logger.warning("谐音表字母 %r 归一化后不是单字母，已跳过", letter)
                continue
            # 字母自身永远可解析
            for reading in [letter, *readings]:
                key = HomophoneMap._key(reading)
                if not key:
                    continue
                existing = reverse.get(key)
                if existing is not None and existing != target:
                    logger.warning(
                        "谐音读音 %r 同时映射到 %r 与 %r，保留先出现的 %r",
                        key, existing, target, existing,
                    )
                    continue
                reverse[key] = target
        return reverse

    # ------------------------------------------------------------------ 匹配

    def _map_token(self, token: str) -> str:
        """单个 token（已归一化的、无空白的片段）→ 字母串。"""
        whole = self._reverse.get(token)
        if whole:
            return whole
        # 未整体命中 → 逐字符最长匹配。纯字母串会逐个命中字母自身（见 _build_reverse_index）
        return self._scan(token)

    def _scan(self, text: str) -> str:
        """逐位置最长匹配：命中读音就替换为字母，未命中保留原字符。"""
        out: list[str] = []
        i = 0
        length = len(text)
        while i < length:
            hit: tuple[str, int] | None = None
            for span in range(min(_MAX_CJK_SPAN, length - i), 0, -1):
                key = text[i : i + span]
                letter = self._reverse.get(key)
                if letter is not None:
                    hit = (letter, span)
                    break
            if hit is not None:
                out.append(hit[0])
                i += hit[1]
            else:
                out.append(text[i])
                i += 1
        return "".join(out)

    def to_letters(self, raw: str | None) -> str:
        """把 ASR 原始文本还原成纯字母序列（需求 §6.2 第 2–4 步）。

        顺序即优先级：**整串 → 多词组合 → 单 token → 逐字符**。
        整串优先是必须的：`"克拉斯"` 逐字符永远得不到 `class`。

        Examples:
            >>> hm = get_map()
            >>> hm.to_letters("see ell aye ess ess")
            'class'
            >>> hm.to_letters("C L A S S")
            'class'
            >>> hm.to_letters("克拉斯")
            'class'
            >>> hm.to_letters("double u a t e r")
            'water'
        """
        normalized = self._key(raw or "")
        if not normalized:
            return ""

        # ① 整串精确匹配
        whole = self.words.get(normalized)
        if whole:
            return whole

        # ② / ③ 按空白切分，做最长多词组合匹配
        tokens = normalized.split(" ")
        pieces: list[str] = []
        i = 0
        count = len(tokens)
        while i < count:
            matched = False
            for span in range(min(_MAX_CJK_SPAN, count - i), 0, -1):
                key = " ".join(tokens[i : i + span])
                letter = self.tokens.get(key)
                if letter:
                    pieces.append(letter)
                    i += span
                    matched = True
                    break
            if not matched:
                pieces.append(self._map_token(tokens[i]))
                i += 1

        return normalize_letters("".join(pieces))

    # ------------------------------------------------------------------ 调试

    def reverse_size(self) -> int:
        """反查表大小（供烟测断言表已真正加载）。"""
        return len(self._reverse)


_map: HomophoneMap | None = None
_map_lock = threading.RLock()


def get_map() -> HomophoneMap:
    """进程内单例。表是只读数据，加载一次即可。"""
    global _map
    with _map_lock:
        if _map is None:
            _map = HomophoneMap.from_file()
        return _map


def reload_map(path: Path | str = _DATA_FILE) -> HomophoneMap:
    """重新加载（改完 yaml 后热更新用）。"""
    global _map
    with _map_lock:
        _map = HomophoneMap.from_file(path)
        return _map
