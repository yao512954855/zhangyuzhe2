"""热词组装 —— WP-BE-06 F11。

**一期不启用**（预研实测对本模型无效，`qa/ASR预研报告.md` §4.2）。

保留本模块与 `start.hotwords` 字段的目的：
1. 契约 §6.2 的 `start` 消息里有 `hotwords[]`，客户端会传，服务端要有地方接住并记录；
2. 换厂商 / 换模型后可以**重新验证**热词是否有效，那时只需把 `enabled` 打开。

⚠️ 因此：**默认一律返回空数组**，不要把客户端传来的热词原样转发给云厂商 ——
预研已证明无效，转发只会增加请求体复杂度与成本，且会让「热词到底有没有用」这件事失去对照组。
"""

from __future__ import annotations

from typing import Any, Iterable


def build_hotwords(
    requested: Iterable[str] | None = None,
    *,
    enabled: bool = False,
    sources: list[str] | None = None,
) -> list[str]:
    """组装最终下发给云厂商的热词列表。

    Args:
        requested: 客户端在 `start.hotwords` 里传的候选词。
        enabled: `asr.yaml` 的 `hotwords.enabled`（一期为 `False`）。
        sources: 预留的服务端热词来源（`asr.yaml` 的 `hotwords.sources`）。

    Returns:
        一期恒为 `[]`；`enabled=True` 时返回去重后的候选（客户端 + 服务端来源）。
    """
    if not enabled:
        return []
    out: list[str] = []
    for item in list(requested or []) + list(sources or []):
        word = str(item or "").strip()
        if word and word not in out:
            out.append(word)
    return out


def current_hotwords(asr_raw: dict[str, Any] | None = None) -> list[str]:
    """按 `asr.yaml` 的 `hotwords` 段取服务端热词（一期恒为空）。"""
    section = (asr_raw or {}).get("hotwords") or {}
    return build_hotwords(
        None,
        enabled=bool(section.get("enabled")),
        sources=list(section.get("sources") or []),
    )
