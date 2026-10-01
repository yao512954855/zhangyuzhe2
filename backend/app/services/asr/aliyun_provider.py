"""阿里云百炼（DashScope）ASR Provider —— WP-BE-06 F2。

**按场景双模型**（2026-09-23 选型实测，依据 `qa/ASR模型选型报告.md`）：

| 场景 | 调用方式 | 模型配置项 | 端点 |
|---|---|---|---|
| 语文背诵（要逐字点亮） | **流式 WebSocket** | `streaming_model` | `ws_endpoint`（`/api-ws/v1/inference`） |
| 英语说词 / 拼读 / 英译中 | **非流式 HTTP** | `batch_model` | `http_endpoint` |

⚠️ 两个必守的坑（选型报告 §3.1 / §3.2）：

1. **HTTP 调用必须显式传 `parameters.format`**，否则报 `UNSUPPORTED_FORMAT: format is empty`。
   本项目传 `http_format`（默认 `wav`），而音频本体是 16k/单声道/16bit 裸 PCM。
2. **流式与非流式的响应结构不同**，解析要分别处理（本文件 `_parse_http` / `_parse_ws_event`）。

⛔ **禁用清单**：`qwen3-asr-flash` —— 会把学生错读「脑补」成正确词（`GLASS`→`CLASS`），
用于判分等于放过错读，属教学事故。本 Provider 在 `start_session` 做**硬拦截**，
防止有人改配置时误用（见 `DISABLED_MODELS`）。

**音频不落盘**（F18）：非流式需要整段提交，PCM 只缓存在内存（`bytearray`），
`close()` 后即释放；进程退出即消失。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx

from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.services.asr.base import (
    ASRError,
    ASRProvider,
    ASRTaskConfig,
    audio_to_data_uri,
)

logger = get_logger(__name__)

#: 禁用模型（契约 §9.2 / 选型报告 §1.2）—— 参与判分会放过错读
DISABLED_MODELS: frozenset[str] = frozenset({"qwen3-asr-flash"})

#: 需要走流式的题型（语文背诵要逐字点亮）
_STREAMING_TASKS: frozenset[str] = frozenset({"recite"})


class AliyunProvider(ASRProvider):
    """阿里云百炼：流式（背诵）+ 非流式（说词/拼读）双通道。"""

    name = "aliyun"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        cfg = self.config.get("aliyun") or self.config
        self.api_key: str = str(cfg.get("api_key") or "")
        self.streaming_model: str = str(cfg.get("streaming_model") or "")
        self.batch_model: str = str(cfg.get("batch_model") or "")
        self.verify_model: str = str(cfg.get("verify_model") or "")
        self.ws_endpoint: str = str(cfg.get("ws_endpoint") or "")
        self.http_endpoint: str = str(cfg.get("http_endpoint") or "")
        self.http_format: str = str(cfg.get("http_format") or "wav")
        self.sample_rate: int = int(cfg.get("sample_rate") or 16000)
        self._timeout_ms: int = int(self.config.get("connect_timeout_ms") or 3000)

        self._streaming = False
        self._pcm = bytearray()      # 非流式：内存暂存整段音频（不落盘）
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._acc_text = ""
        # 流式：按 `sentence_id` 累积各句文本。
        # ⚠️ 云端 `sentence.text` 是**该句的累计全文**，且会**回改前面的字**
        #    （实测 "正是活" → "正是祸不单行的"），所以不能用
        #    `text.startswith(acc)` 这类前缀启发式判断「增量 vs 全文」——
        #    同句内一回改就误判成增量 → 整段被重复拼接（2026-09-29 真机前自检抓到）。
        self._sent_texts: dict[int, str] = {}
        self._task_id = uuid.uuid4().hex
        self._finished_evt: asyncio.Event | None = None
        self._final_text: str | None = None
        self._failure: ASRError | None = None

    @property
    def _selected_model(self) -> str:
        return self.streaming_model if self._streaming else self.batch_model

    def select_model(self, task_type: str) -> str:
        """按题型挑模型：**背诵走流式，其余走非流式**。

        单独抽成公开方法是为了**可测** —— 模型选择是纯决策，
        不应该为了断言它而去连云端（`start_session` 会真的建连）。
        """
        return self.streaming_model if task_type in _STREAMING_TASKS else self.batch_model

    # ------------------------------------------------------------------ 生命周期

    async def start_session(self, cfg: ASRTaskConfig) -> None:
        self.cfg = cfg
        self._streaming = cfg.task_type in _STREAMING_TASKS
        self.model = self.select_model(cfg.task_type)
        # provider 实例由 router 复用 → 每次 start 必须清空累积态
        self._acc_text = ""
        self._sent_texts = {}
        self._final_text = None
        self._failure = None

        if not self.api_key:
            raise ASRError(ErrorCode.ASR_UNAVAILABLE, "未配置百炼 api_key", status="error")
        if not self.model:
            which = "streaming_model" if self._streaming else "batch_model"
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE, f"asr.yaml 未配置 {which}", status="error"
            )
        if self.model in DISABLED_MODELS:      # ⛔ 硬拦截
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE,
                f"模型 {self.model} 在禁用清单中（会把错读脑补成正确词），不得参与判分",
                status="error",
            )

        # 语种：**只用 `cfg.lang_hint`**，它已由网关按 task_type 定了（契约 §6.3）
        if self._streaming:
            await self._open_ws()
        # 非流式：音频在 push_audio 里攒，close() 时一次性提交

    async def push_audio(self, chunk: bytes) -> None:
        n = await self._push_checked(chunk)
        if n == 0:
            return
        if self._streaming:
            if self._ws is None:
                raise ASRError(ErrorCode.ASR_UNAVAILABLE, "云端会话未建立", status="error")
            try:
                await self._ws.send(chunk)
            except Exception as exc:  # noqa: BLE001 - 连接被对端关闭
                raise ASRError(
                    ErrorCode.ASR_UNAVAILABLE, f"推送音频失败：{exc}", status="error"
                ) from exc
        else:
            self._pcm.extend(chunk)

    async def close(self) -> None:
        if self._closed:
            return
        try:
            if self._streaming:
                await self._finish_stream()
            else:
                await self._finish_http()
        finally:
            self._closed = True
            await self._teardown()

    # ------------------------------------------------------------------ 非流式 HTTP

    async def _finish_http(self) -> None:
        if self._aborted:
            return
        if not self._pcm:
            self._emit_final("", None, self.audio_ms)
            return
        payload = {
            "model": self.model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"audio": audio_to_data_uri(bytes(self._pcm), self.http_format, self.sample_rate)}
                        ],
                    }
                ]
            },
            "parameters": {
                # ⚠️ 必须显式传 format，否则 UNSUPPORTED_FORMAT（选型报告 §3.1）
                "format": self.http_format,
                "sample_rate": self.sample_rate,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "disable",
        }
        timeout = max(1.0, self._timeout_ms / 1000.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self.http_endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ASRError(
                ErrorCode.ASR_TIMEOUT, "云端识别超时", status="timeout", action="keyboard"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE, f"云端请求失败：{exc}", status="error"
            ) from exc

        if resp.status_code >= 400:
            # ⚠️ 400 的响应体是定位关键（如 DECODE_ERROR = 音频壳不对），
            #    除抛给网关外必须落日志，否则 asr_logs 只有 code 排不了因。
            logger.error(
                "ASR HTTP 批量识别失败：%s %s model=%s audio_ms=%s resp=%s",
                resp.status_code,
                self.http_endpoint,
                self.model,
                self.audio_ms,
                resp.text[:300],
            )
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE,
                f"云端返回 {resp.status_code}：{resp.text[:200]}",
                status="error",
            )
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE, "云端响应不是合法 JSON", status="error"
            ) from exc

        text, conf = self._parse_http(body)
        self._emit_final(text, conf, self.audio_ms)

    @staticmethod
    def _parse_http(body: dict[str, Any]) -> tuple[str, float | None]:
        """解析非流式响应。

        兼容两种结构（选型报告 §3.1 明确二者**不同**）：

        - `qwen-audio-3.0-asr-flash` / `fun-asr-flash`：`output.output.sentence.text`
          或 `output.sentence.text`
        - 通用兜底：`output.text` / `choices[0].message.content`
        """
        out = body.get("output") or {}
        inner = out.get("output") if isinstance(out.get("output"), dict) else out
        sentence = inner.get("sentence") if isinstance(inner, dict) else None
        if isinstance(sentence, dict) and sentence.get("text") is not None:
            return str(sentence["text"]).strip(), _as_float(sentence.get("confidence"))

        if isinstance(inner, dict) and inner.get("text") is not None:
            return str(inner["text"]).strip(), _as_float(inner.get("confidence"))

        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            msg = (choices[0] or {}).get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                return content.strip(), None
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict) and first.get("text") is not None:
                    return str(first["text"]).strip(), None
        return "", None

    # ------------------------------------------------------------------ 流式 WS

    async def _open_ws(self) -> None:
        import websockets  # 局部导入：非流式路径不依赖它

        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = max(1.0, self._timeout_ms / 1000.0)
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self.ws_endpoint,
                    additional_headers=headers,
                    max_size=None,
                    ping_interval=20,
                    ping_timeout=20,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ASRError(
                ErrorCode.ASR_TIMEOUT, "连接云端超时", status="timeout", action="keyboard"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ASRError(
                ErrorCode.ASR_UNAVAILABLE, f"连接云端失败：{exc}", status="error"
            ) from exc

        run_task = {
            "header": {
                "action": "run-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self.model,
                "parameters": {"sample_rate": self.sample_rate, "format": "pcm"},
                "input": {},
            },
        }
        await self._ws.send(json.dumps(run_task))

        self._finished_evt = asyncio.Event()
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        """后台读 WS 事件，拼**累计文本**后回调 `partial`。"""
        try:
            async for raw in self._ws:
                if not isinstance(raw, str):
                    continue
                try:
                    evt = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                header = evt.get("header") or {}
                event = str(header.get("event") or "")
                if event == "result-generated":
                    text, sentence_id = self._parse_ws_event(evt)
                    if text:
                        # 分片结果 → 累计文本（ReciteEngine.feed 吃累计文本）
                        self._acc_text = self._accumulate(text, sentence_id)
                        self._emit_partial(self._acc_text)
                elif event in ("task-finished", "task-failed"):
                    if event == "task-failed":
                        self._failure = ASRError(
                            ErrorCode.ASR_UNAVAILABLE,
                            f"云端任务失败：{header.get('error_message') or ''}",
                            status="error",
                        )
                    self._final_text = self._acc_text
                    if self._finished_evt is not None:
                        self._finished_evt.set()
                    return
        except Exception as exc:  # noqa: BLE001 - 连接中断
            self._failure = ASRError(
                ErrorCode.ASR_UNAVAILABLE, f"云端连接中断：{exc}", status="error"
            )
            if self._finished_evt is not None:
                self._finished_evt.set()

    def _accumulate(self, text: str, sentence_id: int | None) -> str:
        """把云端分片拼成**累计文本**。

        云端 `sentence.text` 是**该句的累计全文**，且会**回改前面的字**
        （实测 "正是活" → "正是祸不单行的"）。所以：

        - 有 `sentence_id` → **同 id 覆盖、新 id 追加**（按 id 升序拼接）；
        - 没有 `sentence_id`（老格式 / 异常） → 退化为前缀启发式，保底不丢字。
        """
        if sentence_id is None:
            return text if text.startswith(self._acc_text) else self._acc_text + text
        self._sent_texts[sentence_id] = text
        return "".join(self._sent_texts[k] for k in sorted(self._sent_texts))

    @staticmethod
    def _parse_ws_event(evt: dict[str, Any]) -> tuple[str, int | None]:
        """取 `payload.output.sentence`（选型报告 §3.2）→ `(text, sentence_id)`。"""
        out = (evt.get("payload") or {}).get("output") or {}
        sentence = out.get("sentence")
        if isinstance(sentence, dict) and sentence.get("text") is not None:
            raw_sid = sentence.get("sentence_id")
            try:
                sid = int(raw_sid) if raw_sid is not None else None
            except (TypeError, ValueError):
                sid = None
            return str(sentence["text"]), sid
        if out.get("text") is not None:
            return str(out["text"]), None
        return "", None

    async def _finish_stream(self) -> None:
        if self._ws is None or self._aborted:
            return
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "header": {
                            "action": "finish-task",
                            "task_id": self._task_id,
                            "streaming": "duplex",
                        },
                        "payload": {"input": {}},
                    }
                )
            )
        except Exception:  # noqa: BLE001 - 连接已断，下面统一按失败处理
            pass

        if self._finished_evt is not None:
            # 非流式把「整段音频提交 → 拿结果」算作一次调用；流式的等价窗口就是收尾等待
            wait_s = max(1.0, self._timeout_ms / 1000.0)
            try:
                await asyncio.wait_for(self._finished_evt.wait(), timeout=wait_s)
            except asyncio.TimeoutError as exc:
                raise ASRError(
                    ErrorCode.ASR_TIMEOUT, "等待云端结果超时", status="timeout",
                    action="keyboard",
                ) from exc

        if self._failure is not None:
            raise self._failure
        self._emit_final(self._final_text or self._acc_text, None, self.audio_ms)

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
        self._pcm = bytearray()     # 用完即弃（F18：不落盘、不留存）


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
