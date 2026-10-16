"""离线联调用 ASR Provider —— WP-BE-06 F4。

**这个 Provider 是 APP-02/03/04 能并行开发的前提**：没有云账号也能跑通
`start → audio → end → verdict` 全流程，且**行为可脚本化**，是所有 BE-06 自动化用例的底座。

配置（`asr.yaml` 的 `mock:` 段，或直接给构造函数传 dict）：

```yaml
mock:
  behavior: normal          # normal | empty | timeout | error | connect_fail
  fail_times: 0             # 前 N 次会话直接失败（测主备切换 U9 / 连续错误 U8）
  partial_script: []        # 累计 partial 文本；空则自动从 final_text 切分
  final_text: ""            # 最终文本
  confidence: 0.95
  partial_every: 1          # 每 N 片音频产出一个 partial
  error_code: 50001
  by_task:                  # 按 task_type 覆盖
    spell: {final_text: "see ell aye ess ess"}
```

行为语义：

| behavior | start_session | 音频期 | close() |
|---|---|---|---|
| `normal` | 成功 | 按脚本产 `partial` | 产 `utterance`（模拟本地引擎语句级语义）+ `final` |
| `empty` | 成功 | 无 | `final=""`（随后由网关报 50003，**不计错**） |
| `timeout` | 成功 | 无 | 抛 `50002` / `action=keyboard` |
| `error` | 成功 | 无 | 抛 `50001` |
| `connect_fail` | 抛 `50001`（模拟建连失败 → 触发主备切换） | — | — |

> `close()` 多产的那条 `utterance` 是为覆盖 v1.3 §5 verdict 链路（契约修订
> v1.3：松开后整词判定，utterance 只透传回显并充当 verdict 的转写来源）：
> Mock 在 close 时一次性补上，保证 WS 层用例不依赖真模型。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ErrorCode
from app.services.asr.base import ASRError, ASRProvider, ASRTaskConfig


class MockProvider(ASRProvider):
    """按预置脚本返回 `partial` / `final`，不依赖网络。"""

    name = "mock"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        #: 与 AliyunProvider 一致：优先取 `mock:` 段；没有该段时把整个 dict 当配置
        #: （单测里常常直接传扁平 dict，两种写法都要能用）
        self._base: dict[str, Any] = dict(self.config.get("mock") or self.config)
        self.model = str(self._base.get("model") or "mock-asr")
        #: `start_session` 被真正调用的次数（U8「不再尝试云」靠它断言）
        self.start_calls = 0
        self._chunk_count = 0
        self._script: list[str] = []
        self._script_pos = 0
        self._final_text: str = ""
        self._behavior: str = "normal"
        self._partial_every: int = 1
        self._confidence: float | None = None
        self._error_code: int = int(ErrorCode.ASR_UNAVAILABLE)
        #: 前 N 次 `start_session` 强制失败（U8 连续错误 / U9 主备切换）
        self._fail_left: int = int(self._base.get("fail_times") or 0)
        #: 重听式判分（2026-10-16）预置转写：`offline_decode()` 返回值，
        #: 默认空 = 判分侧自动回退纯流式（旧行为不变）
        self._rehear_text: str = ""

    # ------------------------------------------------------------------ 生命周期

    async def start_session(self, cfg: ASRTaskConfig) -> None:
        self.start_calls += 1
        self.cfg = cfg

        # 前 N 次会话强制失败（用于 U8/U9 的确定性构造）
        if self._fail_left > 0:
            self._fail_left -= 1
            raise ASRError(self._error_code, "Mock：预置的建连失败", status="error")

        override = (self._base.get("by_task") or {}).get(cfg.task_type) or {}
        merged = {**self._base, **override}

        self._behavior = str(merged.get("behavior") or "normal")
        self._partial_every = max(1, int(merged.get("partial_every") or 1))
        self._confidence = merged.get("confidence", 0.95)
        self._error_code = int(merged.get("error_code") or ErrorCode.ASR_UNAVAILABLE)

        final_text = str(merged.get("final_text") or "")
        if self._behavior == "empty":
            final_text = ""
        self._final_text = final_text
        self._rehear_text = str(merged.get("rehear_text") or "")

        script = [str(x) for x in (merged.get("partial_script") or [])]
        if not script and final_text:
            script = self._split_script(final_text)
        self._script = script
        self._script_pos = 0

        if self._behavior == "connect_fail":
            raise ASRError(self._error_code, "Mock：主厂商建连失败", status="error")

    async def push_audio(self, chunk: bytes) -> None:
        self._chunk_count += 1
        await self._push_checked(chunk)      # 规格校验（F6/U5）
        if self._behavior != "normal":
            return
        if self._chunk_count % self._partial_every:
            return
        if self._script_pos < len(self._script):
            self._emit_partial(self._script[self._script_pos])
            self._script_pos += 1

    def offline_decode(self) -> str:
        """重听式判分（2026-10-16）：返回预置 `rehear_text`（默认空=回退流式）。

        与 LocalProvider.offline_decode 同协议：判分侧 session_manager
        两边择优（重听为主、流式累积兜底）。
        """
        return self._rehear_text

    async def close(self) -> None:
        self._closed = True
        if self._behavior == "timeout":
            raise ASRError(
                ErrorCode.ASR_TIMEOUT,
                "Mock：识别超时",
                status="timeout",
                action="keyboard",      # 契约 §6.5：云接口超时 > 3s → 切键盘
            )
        if self._behavior in ("error", "connect_fail"):
            raise ASRError(self._error_code, "Mock：识别失败", status="error")

        # 把脚本里剩余的部分作为「最终累计文本」下发
        text = self._final_text or (self._script[-1] if self._script else "")
        # 模拟本地引擎（Vosk）的语句级语义：close 时把最终文本也作为一条
        # utterance 下发 —— v1.3 zh2en 的整词判定（verdict）以 utterance 累积
        # 为完整转写来源（契约 §5 v1.3）。云端厂商无此语义；不注册 utterance
        # 回调或非 spell_zh2en 模式时天然无副作用。
        if self._behavior == "normal" and text:
            self._emit_utterance(text)
        self._emit_final(text, self._confidence, self.audio_ms)

    # ------------------------------------------------------------------ 内部

    def _split_script(self, final_text: str) -> list[str]:
        """无显式脚本时，按字符/词渐进切分，模拟流式中间结果。

        中文按**字符**累进（逐字点亮最小粒度就是字）；含空格的内容按**词**累进。
        """
        tokens = final_text.split() if " " in final_text.strip() else list(final_text)
        sep = " " if " " in final_text.strip() else ""
        out: list[str] = []
        for i in range(1, len(tokens) + 1):
            out.append(sep.join(tokens[:i]))
        # 最后一个 partial 之后还会发 final，故丢掉与 final 完全相同的那一档
        return out[:-1] if len(out) > 1 else out

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MockProvider behavior={self._behavior!r} calls={self.start_calls}>"
