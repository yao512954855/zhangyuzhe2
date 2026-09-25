"""火山引擎（豆包）流式语音识别 Provider —— WP-BE-06 F3。

**现状说明（重要，别把它当已验证实现）**：
火山侧配置项齐全，但账号受**企业实名限制**，本期**未拿到可用凭证做真机联调**。
因此本 Provider 的设计目标是「**配置对了就能用、配置不对立刻给出明确错误**」，
而不是「静默降级成空结果」——后者会让学生以为是自己念错了。

错误语义：

| 情况 | 行为 |
|---|---|
| 缺 `app_id` / `access_token` | `start_session` 抛 `ASRError(50001)`，消息指明缺哪个字段 |
| 缺 `endpoint` | 同上 |
| 建连失败 / 超时 | `ASR_TIMEOUT`（`action=keyboard`）或 `ASR_UNAVAILABLE`，**带底层原因** |

协议按火山官方流式 ASR 的通用形态实现（二进制音频帧 + JSON 结果帧）；
**接入真实凭证后必须重新校准解析逻辑**（同 `spell→zh` 那条结论的处理方式）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from app.core.errors import ErrorCode
from app.services.asr.base import ASRError, ASRProvider, ASRTaskConfig


class VolcengineProvider(ASRProvider):
    """火山豆包流式 ASR。凭证缺失时**明确报错**，不静默返回空。"""

    name = "volcengine"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        cfg = self.config.get("volcengine") or self.config
        self.app_id: str = str(cfg.get("app_id") or "")
        self.access_token: str = str(cfg.get("access_token") or "")
        self.model: str = str(cfg.get("model") or "doubao-streaming-asr")
        self.endpoint: str = str(cfg.get("endpoint") or "")
        self._timeout_ms: int = int(self.config.get("connect_timeout_ms") or 3000)

        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._acc_text = ""
        self._request_id = str(uuid.uuid4())
        self._finished_evt: asyncio.Event | None = None
        self._failure: ASRError | None = None

    def _check_credentials(self) -> None:
        missing: list[str] = []
        if not self.app_id:
            missing.append("volcengine.app_id")
        if not self.access_token:
            missing.append("volcengine.access_token")
        if not self.endpoint:
            missing.append("volcengine.endpoint")
        if missing:
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE,
                "火山引擎语音未配置完整，缺少：" + "、".join(missing),
                status="error",
            )

    # ------------------------------------------------------------------ 生命周期

    async def start_session(self, cfg: ASRTaskConfig) -> None:
        self.cfg = cfg
        self._check_credentials()

        import websockets  # 局部导入，避免未装时的 import 期失败

        headers = {
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": "volc.bigasr.sauc.duration",
            "X-Api-Request-Id": self._request_id,
        }
        timeout = max(1.0, self._timeout_ms / 1000.0)
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self.endpoint,
                    additional_headers=headers,
                    max_size=None,
                    ping_interval=20,
                    ping_timeout=20,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ASRError(
                ErrorCode.ASR_TIMEOUT, "连接火山引擎超时", status="timeout", action="keyboard"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 企业实名限制常见于此
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE,
                f"连接火山引擎失败（可能是企业实名限制未开通）：{exc}",
                status="error",
            ) from exc

        # 会话配置帧
        await self._ws.send(
            json.dumps(
                {
                    "request_id": self._request_id,
                    "model_name": self.model,
                    "audio": {
                        "format": "pcm",
                        "sample_rate": cfg.sample_rate,
                        "channel": 1,
                        "bits": 16,
                    },
                }
            )
        )
        self._finished_evt = asyncio.Event()
        self._reader = asyncio.create_task(self._read_loop())

    async def push_audio(self, chunk: bytes) -> None:
        n = await self._push_checked(chunk)
        if n == 0:
            return
        if self._ws is None:
            raise ASRError(ErrorCode.ASR_UNAVAILABLE, "云端会话未建立", status="error")
        try:
            await self._ws.send(chunk)
        except Exception as exc:  # noqa: BLE001
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE, f"推送音频失败：{exc}", status="error"
            ) from exc

    async def close(self) -> None:
        if self._closed:
            return
        try:
            if self._aborted:
                return
            if self._ws is not None:
                try:
                    await self._ws.send(json.dumps({"request_id": self._request_id}))
                except Exception:  # noqa: BLE001
                    pass
            if self._finished_evt is not None:
                try:
                    await asyncio.wait_for(
                        self._finished_evt.wait(),
                        timeout=max(1.0, self._timeout_ms / 1000.0),
                    )
                except asyncio.TimeoutError as exc:
                    raise ASRError(
                        ErrorCode.ASR_TIMEOUT,
                        "等待火山引擎结果超时",
                        status="timeout",
                        action="keyboard",
                    ) from exc
            if self._failure is not None:
                raise self._failure
            self._emit_final(self._acc_text, None, self.audio_ms)
        finally:
            self._closed = True
            await self._teardown()

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    evt = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                text = self._parse_event(evt)
                if text:
                    self._acc_text = text if text.startswith(self._acc_text) \
                        else self._acc_text + text
                    self._emit_partial(self._acc_text)
                if evt.get("is_final") or evt.get("code") in (0, 1000, 20000000):
                    if evt.get("is_final"):
                        if self._finished_evt is not None:
                            self._finished_evt.set()
                        return
        except Exception as exc:  # noqa: BLE001
            self._failure = ASRError(
                ErrorCode.ASR_UNAVAILABLE, f"火山引擎连接中断：{exc}", status="error"
            )
            if self._finished_evt is not None:
                self._finished_evt.set()

    @staticmethod
    def _parse_event(evt: dict[str, Any]) -> str:
        """取识别文本（兼容 `result[].text` / `text` / `payload.msg` 三种形态）。"""
        result = evt.get("result")
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict) and first.get("text") is not None:
                return str(first["text"])
        if evt.get("text") is not None:
            return str(evt["text"])
        payload = evt.get("payload")
        if isinstance(payload, dict) and payload.get("text") is not None:
            return str(payload["text"])
        return ""

    async def _teardown(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader = None
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
