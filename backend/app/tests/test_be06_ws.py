"""WP-BE-06 WebSocket 网关 —— 验收测试（10b U1 ~ U16）。

分两层，**故意这么分**：

| 层 | 手段 | 覆盖 |
|---|---|---|
| 会话状态机 | 直接驱动 `ASRSession` + 假 `send` | U2~U9、U12、U14~U16（快、确定） |
| 真端点 | `TestClient.websocket_connect`（走真 ASGI 握手） | U1 关闭码、U13 心跳、U11 踢旧连接 |

真正的**真 uvicorn + 真 TCP** 端到端在 `backend/scripts/smoke_be06.py`。

### 三个必须自己处理的坑（与 BE-03/04/05 相同）

1. `os.environ` 是进程级的 → 每个 fixture 重设 + **断言库路径是自己的**。
2. `SettingsService._cache` 是类变量 → 换库必须清。
3. **`asr.yaml` 是从磁盘读的** → 用 `ASR_CONFIG_PATH` 指到临时配置，
   且 `api/asr_ws.get_gate()` 会缓存闸门，换配置后必须 `reset_gate()`。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="bjsys_be06ws_"))
_DB_FILE = _TMP_DIR / "test.db"
_ASR_YAML = _TMP_DIR / "asr-test.yaml"

os.environ["DB_PATH"] = str(_DB_FILE)
os.environ["BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["ASR_CONFIG_PATH"] = str(_ASR_YAML)
# 本批用例是云端路径验收（mock 厂商流）。本地模型就位后 hybrid 会优先 local，
# 必须按契约 §2 用部署锁把 ASR 钉死在 cloud（每次调用现读 env，模块级设置即生效）。
os.environ["CAP_ASR"] = "cloud"

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from app.api import asr_ws as asr_ws_api  # noqa: E402
from app.core import rate_limit  # noqa: E402
from app.core.config import get_settings, reset_config_cache  # noqa: E402
from app.core.db import read_conn, utc_now_iso, write_tx  # noqa: E402
from app.core.deps import CurrentUser  # noqa: E402
from app.core.errors import ErrorCode  # noqa: E402
from app.main import app  # noqa: E402
from app.services.asr.base import BYTES_PER_MS  # noqa: E402
from app.services.asr.session_manager import (  # noqa: E402
    ASRSession,
    GlobalGate,
    SessionLimitError,
    SessionRegistry,
)
from app.services.settings_service import SettingsService  # noqa: E402

CHUNK = b"\x00\x00" * (150 * BYTES_PER_MS // 2)      # 150ms @16k/16bit/mono


# ---------------------------------------------------------------------------
# 配置与 fixture
# ---------------------------------------------------------------------------

def _write_asr_yaml(
    mock: dict[str, Any] | None = None,
    top: dict[str, Any] | None = None,
) -> None:
    """写临时 `asr.yaml`。

    Args:
        mock: `mock:` 段内的覆盖（行为脚本）。
        top: 顶层覆盖（`provider` / `arbitrator` / `limits` 等）。

    ⚠️ 两个参数**必须分开**：早先把 `arbitrator` 传进 `mock:` 段，
    结果仲裁看着「已启用」实际没生效 —— 这类静默失效最难查。
    """
    body: dict[str, Any] = {
        "provider": "mock",
        "lang_map": {"word": "en", "spell": "zh", "zh_answer": "zh", "recite": "zh"},
        "mock": {
            "final_text": "classroom",
            "partial_every": 2,
            "by_task": {
                "spell": {"final_text": "see ell aye ess ess"},
                "zh_answer": {"final_text": "教室"},
                "recite": {"final_text": "我与父亲不相见"},
            },
            **(mock or {}),
        },
        "hotwords": {"enabled": False},
        "arbitrator": {"enabled": False},
        "limits": {
            "max_connections": 60,
            "queue_size": 120,
            "queue_wait_ms": 300,
            "heartbeat_timeout_s": 60,
            "max_session_seconds": 300,
            "chunk_ms": 150,
        },
    }
    if top:
        body.update(top)
    _ASR_YAML.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _seed_session(user_id: int, content_type: str = "english") -> int:
    """返回会话 id。

    简化版 `sessions` 表已随 WP-F 清理删除，`asr_logs.session_id` 改为普通整型
    （无外键约束），故无需真实插库，直接返回固定 id 即可。
    """
    return 1


#: `TestClient` 需要请求头级别的 token；WS 用 query
_user_id: int | None = None


@pytest.fixture()
def client() -> Any:
    """每个测试一个全新的空库 + 干净的限流/配置/会话注册表。"""
    for key, value in {
        "DB_PATH": str(_DB_FILE),
        "BACKUP_DIR": str(_TMP_DIR / "backups"),
        "LOG_LEVEL": "WARNING",
        "ASR_CONFIG_PATH": str(_ASR_YAML),
    }.items():
        os.environ[key] = value
    reset_config_cache()
    assert get_settings().db_file == _DB_FILE, "DB_PATH 被别的测试模块改掉了"

    for suffix in ("", "-wal", "-shm"):
        p = Path(str(_DB_FILE) + suffix)
        if p.exists():
            p.unlink()

    _write_asr_yaml()
    SettingsService._cache = None
    rate_limit.clear_all()
    asr_ws_api.reset_gate()

    with TestClient(app) as c:
        yield c


def _make_student(client: TestClient, username: str = "stu1") -> tuple[int, str]:
    """简化版无登录态：返回固定占位用户 id 与任意 token（WS 不再鉴权）。

    原实现调 `/api/auth/register` 并返回 JWT，WP-F 拆鉴权后该端点已删除，
    故改为直接返回占位值。WS 端点对 token 不做校验（见 `core/deps.py`）。
    """
    return 1, "local"


class FakeSession:
    """驱动 `ASRSession` 的最小夹具：收集出站消息，不发真帧。"""

    def __init__(
        self,
        user_id: int,
        *,
        mock_extra: dict[str, Any] | None = None,
        top_extra: dict[str, Any] | None = None,
        session_id: int | None = None,
    ) -> None:
        _write_asr_yaml(mock_extra, top_extra)
        self.session_id = session_id if session_id is not None else _seed_session(user_id)
        self.user = CurrentUser(id=user_id, role="student", username="stu",
                                real_name="同学", class_id=None)
        self.sent: list[dict[str, Any]] = []
        self.session = ASRSession(user=self.user, send=self._send)

    async def _send(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)

    async def start(self, **over: Any) -> None:
        payload: dict[str, Any] = {
            "type": "start",
            "session_id": self.session_id,
            "task_type": "word",
            "lang_hint": "en",
            "word_id": 1,
            "expected": "classroom",
        }
        payload.update(over)
        await self.session.handle_text(json.dumps(payload))
        await self.session.flush()

    async def audio(self, n: int = 3) -> None:
        for _ in range(n):
            await self.session.handle_bytes(CHUNK)
        await self.session.flush()

    async def end(self) -> None:
        await self.session.handle_text(json.dumps({"type": "end"}))
        await self.session.flush()

    async def ping(self) -> None:
        await self.session.handle_text(json.dumps({"type": "ping"}))
        await self.session.flush()

    async def cancel(self) -> None:
        await self.session.handle_text(json.dumps({"type": "cancel"}))
        await self.session.flush()

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]

    def first(self, mtype: str) -> dict[str, Any] | None:
        return next((m for m in self.sent if m["type"] == mtype), None)

    def last(self, mtype: str) -> dict[str, Any] | None:
        got = [m for m in self.sent if m["type"] == mtype]
        return got[-1] if got else None


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ===========================================================================
# U2 / U3：主流程与拼读差异回显
# ===========================================================================

class TestMainFlow:
    def test_word_flow_yields_started_partial_final_verdict(self, client: TestClient) -> None:
        """U2：started → partial → final → verdict，`is_correct` 正确。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.start()
            await fs.audio(4)
            await fs.end()

        run(flow())
        order = fs.types()
        assert order[0] == "started", order
        assert "partial" in order, "语文逐字点亮依赖 partial，说词也必须给中间结果"
        assert order.index("final") < order.index("verdict"), order

        verdict = fs.last("verdict")
        assert verdict is not None
        assert verdict["is_correct"] is True
        assert verdict["match_type"] == "exact"
        assert verdict["asr_text"] == "classroom"
        started = fs.first("started")
        assert started is not None and started["provider"] == "mock"
        assert started["sample_rate"] == 16000 and started["chunk_ms"] == 150

    def test_spell_flow_diff_is_positional(self, client: TestClient) -> None:
        """U3：Mock 返回「see ell aye ess ess」→ 逐位 diff 正确。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.start(task_type="spell", expected="class", lang_hint="en")
            await fs.audio(4)
            await fs.end()

        run(flow())
        verdict = fs.last("verdict")
        assert verdict is not None, fs.sent
        assert verdict["is_correct"] is True
        assert verdict["match_type"] == "exact"
        # 谐音表把「see ell aye ess ess」还原成 class
        assert verdict["normalized"] == "class"
        assert [d["state"] for d in (verdict["diff"] or [])] == ["match"] * 5

    # 注：recite 三类用例（U4 背诵进度 / settle 竞态 / 过期上报）依赖已删除的
    # `services/recite/` 背诵引擎（WP-F F-2 删除清单），故移除。


# ===========================================================================
# U5 ~ U8：音频规格、空结果、超时、连续失败切键盘
# ===========================================================================

class TestFailurePaths:
    def test_bad_audio_returns_50004(self, client: TestClient) -> None:
        """U5：非法音频 → 50004，且**不静默**。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.start()
            await fs.session.handle_bytes(b"\x00\x00\x00")   # 奇数长度
            await fs.session.flush()

        run(flow())
        err = fs.last("error")
        assert err is not None and err["code"] == ErrorCode.ASR_BAD_AUDIO
        rows = _logs(client)
        assert rows[-1]["status"] == "error"
        assert rows[-1]["error_code"] == str(ErrorCode.ASR_BAD_AUDIO)

    def test_empty_result_is_retry_and_not_counted(self, client: TestClient) -> None:
        """U6：云返回空 → 50003 + retry；`asr_logs.status='empty'`；**该题不计错**。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid, mock_extra={"behavior": "empty"})

        async def flow() -> None:
            await fs.start()
            await fs.audio(3)
            await fs.end()

        run(flow())
        err = fs.last("error")
        assert err is not None and err["code"] == ErrorCode.ASR_EMPTY
        assert err["action"] == "retry"
        assert fs.last("verdict") is None, "空结果不得产生 verdict（否则会被算成判错）"
        assert _logs(client)[-1]["status"] == "empty"

    def test_timeout_switches_to_keyboard(self, client: TestClient) -> None:
        """U7：超时 → `action='keyboard'` + `status='timeout'`。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid, mock_extra={"behavior": "timeout"})

        async def flow() -> None:
            await fs.start()
            await fs.audio(2)
            await fs.end()

        run(flow())
        err = fs.last("error")
        assert err is not None and err["code"] == ErrorCode.ASR_TIMEOUT
        assert err["action"] == "keyboard"
        assert _logs(client)[-1]["status"] == "timeout"

    def test_three_consecutive_failures_enter_keyboard_mode(self, client: TestClient) -> None:
        """U8：连续 3 次云错误 → 会话切键盘模式，**之后不再尝试云**。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid, mock_extra={"behavior": "error"})
        start_calls = {"n": 0}
        original = fs.session.router.start

        async def counting_start(cfg: Any) -> Any:
            start_calls["n"] += 1
            return await original(cfg)

        fs.session.router.start = counting_start        # type: ignore[method-assign]

        async def full_task() -> None:
            await fs.start()
            await fs.session.flush()
            await fs.end()
            await fs.session.flush()

        async def flow() -> None:
            for _ in range(3):
                await full_task()
            assert fs.session.keyboard_mode is True

            before = start_calls["n"]
            mark = len(fs.sent)
            # 第 4 次：只发 start —— 断言点必须是**这一条** error
            #（若连 end 也发，最后一条 error 会是「尚未 start 就 end」，把断言带偏）
            await fs.start()
            assert start_calls["n"] == before, "切键盘后不得再尝试云"
            new_msgs = fs.sent[mark:]
            assert new_msgs and new_msgs[0]["type"] == "error"
            assert new_msgs[0]["action"] == "keyboard"
            assert new_msgs[0]["fallback"] == "keyboard"

        run(flow())

    def test_cancel_produces_no_verdict_and_no_log(self, client: TestClient) -> None:
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.start()
            await fs.audio(2)
            await fs.cancel()

        run(flow())
        assert fs.last("verdict") is None, "cancel 不计错，不该判分"
        assert _logs(client) == [], "cancel 不是一次识别调用，不应写 asr_logs"


# ===========================================================================
# U9 / U14 / U16：主备切换、expected 隔离、日志完整性
# ===========================================================================

class TestFailoverAndIsolation:
    def test_backup_provider_takes_over_and_is_logged(self, client: TestClient) -> None:
        """U9：主厂商建连失败 → 备厂商完成识别；`asr_logs.provider` 记实际厂商。"""
        uid, _ = _make_student(client)
        cfg = json.loads(_ASR_YAML.read_text(encoding="utf-8"))
        cfg["provider"] = "not-a-real-provider"
        cfg["backup_provider"] = "mock"
        _ASR_YAML.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        asr_ws_api.reset_gate()

        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.start()
            await fs.audio(4)
            await fs.end()

        run(flow())
        verdict = fs.last("verdict")
        assert verdict is not None and verdict["is_correct"] is True
        assert fs.first("started")["provider"] == "mock"
        assert _logs(client)[-1]["provider"] == "mock"

    def test_expected_never_reaches_cloud(self, client: TestClient) -> None:
        """U14：`expected` 只用于服务端判定，**云侧只收到音频与语种/热词**。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid)
        captured: list[Any] = []
        original = fs.session.router.start

        async def spy(cfg: Any) -> Any:
            captured.append(cfg)
            return await original(cfg)

        fs.session.router.start = spy                  # type: ignore[method-assign]

        async def flow() -> None:
            await fs.start(expected="classroom")
            await fs.audio(4)
            await fs.end()

        run(flow())
        assert captured, "router.start 应被调用"
        cfg = captured[0]
        # 交给云侧的配置里**没有任何答案字段**
        dumped = cfg.model_dump()
        assert "expected" not in dumped
        assert set(dumped) == {
            "session_id", "task_type", "lang_hint", "hotwords",
            "sample_rate", "enable_intermediate",
        }
        assert cfg.hotwords == [], "一期热词不启用，必须传空数组"
        # 但 verdict 里要回传给客户端（判定已完成，不算提前泄题）
        assert fs.last("verdict")["expected"] == "classroom"

    def test_language_is_decided_by_server(self, client: TestClient) -> None:
        """契约 §6.3：语种由**服务端**按 task_type 定，不信客户端 `lang_hint`。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid)
        captured: list[Any] = []
        original = fs.session.router.start

        async def spy(cfg: Any) -> Any:
            captured.append(cfg)
            return await original(cfg)

        fs.session.router.start = spy                  # type: ignore[method-assign]

        async def flow() -> None:
            # 客户端故意谎报 en
            await fs.start(task_type="spell", expected="class", lang_hint="en")
            await fs.session.flush()
            await fs.cancel()

        run(flow())
        assert captured[0].lang_hint == "zh", "拼读必须用 zh（en 会把字母串拼成单词）"

    def test_asr_logs_record_is_complete(self, client: TestClient) -> None:
        """U16：每次识别后 `asr_logs` 记录齐全，含 `latency_ms` 与 `status`。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.start()
            await fs.audio(4)
            await fs.end()

        run(flow())
        rows = _logs(client)
        assert len(rows) == 1
        row = rows[0]
        assert row["provider"] == "mock"
        assert row["status"] == "ok"
        assert row["lang"] == "en"
        assert row["task_type"] == "word"
        assert row["audio_ms"] == 4 * 150
        assert row["latency_ms"] is not None and row["latency_ms"] >= 0
        assert row["session_id"] == fs.session_id
        assert row["student_id"] == uid

    def test_no_audio_written_to_disk(self, client: TestClient) -> None:
        """U15：音频只在内存，**磁盘上不产生任何音频文件**。"""
        uid, _ = _make_student(client)
        before = {p for p in _TMP_DIR.rglob("*") if p.is_file()}
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.start()
            await fs.audio(6)
            await fs.end()

        run(flow())
        after = {p for p in _TMP_DIR.rglob("*") if p.is_file()}
        audio_ext = {".pcm", ".wav", ".mp3", ".webm", ".raw", ".ogg", ".m4a"}
        assert not {p for p in (after - before) if p.suffix.lower() in audio_ext}
        assert fs.session._pcm_window_bytes == 0 or not fs.session._pcm_window


# ===========================================================================
# U10 / U11 / U12 / U13：并发、踢旧连接、重连补发、心跳
# ===========================================================================

class TestConcurrencyAndResume:
    def test_gate_rejects_61st_connection(self, client: TestClient) -> None:
        """U10：60 路并发后第 61 路 → 50001，且**已有连接不受影响**。"""
        async def flow() -> tuple[int, int, bool]:
            gate = GlobalGate(max_connections=60, queue_size=120, wait_ms=200)
            for _ in range(60):
                await gate.acquire()
            assert gate.active == 60
            raised = False
            try:
                await gate.acquire()          # 第 61 路：排队 200ms 后超时
            except SessionLimitError as exc:
                raised = True
                assert exc.code == ErrorCode.ASR_UNAVAILABLE
            active_after = gate.active
            await gate.release()
            return gate.active, active_after, raised

        active, active_after, raised = run(flow())
        assert raised is True
        assert active_after == 60, "被拒的连接不得占用并发位"
        assert active == 59, "已有连接释放后计数正确"

    def test_gate_queue_full_rejected_immediately(self) -> None:
        async def flow() -> bool:
            gate = GlobalGate(max_connections=1, queue_size=1, wait_ms=50)
            await gate.acquire()
            waiter = asyncio.create_task(gate.acquire())     # 占满队列
            await asyncio.sleep(0.01)
            try:
                await gate.acquire()                         # 队列已满 → 立刻拒
            except SessionLimitError:
                waiter.cancel()
                return True
            waiter.cancel()
            return False

        assert run(flow()) is True

    def test_second_connection_kicks_the_old_one(self, client: TestClient) -> None:
        """U11：同账号第二条 WS → 旧连接被标记踢掉。"""
        uid, _ = _make_student(client)
        first = FakeSession(uid)
        second = FakeSession(uid)
        SessionRegistry.register(first.session)
        SessionRegistry.register(second.session)
        assert first.session.kicked is True
        assert second.session.kicked is False
        assert first.session.close_reason is not None
        assert SessionRegistry.active_count() == 1

    def test_reconnect_replays_verdict(self, client: TestClient) -> None:
        """U12：断线重连带 `last_answer_id` → 补发对应 `verdict`。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            # `answer_id` 由客户端在 start 里带上（判定结果要回写答案表时用）
            await fs.start(answer_id=55021)
            await fs.audio(4)
            await fs.end()

        run(flow())
        assert SessionRegistry.get_verdict(55021) is not None

        # 新连接（模拟重连）
        fs2 = FakeSession(uid)

        async def replay() -> None:
            await fs2.session.replay(55021)
            await fs2.session.flush()

        run(replay())
        replayed = fs2.last("verdict")
        assert replayed is not None and replayed["answer_id"] == 55021

    def test_replay_unknown_answer_id_sends_nothing(self, client: TestClient) -> None:
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.session.replay(999999)
            await fs.session.flush()

        run(flow())
        assert fs.sent == []

    def test_heartbeat_expires_after_silence(self, client: TestClient) -> None:
        """U13：60 秒不发消息 → `heartbeat_expired` 为真（端点据此关闭连接）。"""
        uid, _ = _make_student(client)

        async def flow() -> tuple[bool, bool]:
            fs = FakeSession(uid)
            fresh = fs.session.heartbeat_expired
            fs.session.last_activity -= 61
            return fresh, fs.session.heartbeat_expired

        fresh, expired = run(flow())
        assert fresh is False
        assert expired is True

    def test_session_duration_cap(self, client: TestClient) -> None:
        uid, _ = _make_student(client)

        async def flow() -> tuple[bool, bool]:
            fs = FakeSession(uid)
            await fs.start()
            await fs.session.flush()
            ok = fs.session.session_expired
            fs.session.started_at -= 400
            return ok, fs.session.session_expired

        ok, expired = run(flow())
        assert ok is False
        assert expired is True


# ===========================================================================
# 协议细节：ping / 未知类型 / 未 start
# ===========================================================================

class TestProtocolDetails:
    def test_ping_pong(self, client: TestClient) -> None:
        uid, _ = _make_student(client)
        fs = FakeSession(uid)
        run(fs.ping())
        pong = fs.last("pong")
        assert pong is not None and pong["ts_ms"] >= 0

    def test_unknown_message_type_rejected(self, client: TestClient) -> None:
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.session.handle_text(json.dumps({"type": "teleport"}))
            await fs.session.flush()

        run(flow())
        err = fs.last("error")
        assert err is not None and err["code"] == ErrorCode.PARAM_INVALID

    def test_non_json_frame_rejected(self, client: TestClient) -> None:
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.session.handle_text("not json at all")
            await fs.session.flush()

        run(flow())
        assert fs.last("error")["code"] == ErrorCode.PARAM_INVALID

    def test_audio_before_start_rejected(self, client: TestClient) -> None:
        """start 前的音频帧：**静默丢弃**（2026-10-11 起），不再回 error——
        App 常驻录音器 end 后仍推帧，每帧回 error 会把 App 刷成「没听清」死页。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.session.handle_bytes(CHUNK)
            await fs.session.handle_bytes(CHUNK)
            await fs.session.flush()

        run(flow())
        assert fs.last("error") is None, "未 start 的帧必须静默丢弃，不得回 error"

    def test_invalid_task_type_rejected(self, client: TestClient) -> None:
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.start(task_type="sing")
            await fs.session.flush()

        run(flow())
        err = fs.last("error")
        assert err is not None and err["code"] == ErrorCode.PARAM_INVALID

    def test_missing_session_id_rejected(self, client: TestClient) -> None:
        uid, _ = _make_student(client)
        fs = FakeSession(uid)

        async def flow() -> None:
            await fs.session.handle_text(json.dumps({"type": "start", "task_type": "word"}))
            await fs.session.flush()

        run(flow())
        assert fs.last("error")["code"] == ErrorCode.PARAM_INVALID


# ===========================================================================
# 仲裁（F19）在会话层的接线
# ===========================================================================

class TestArbitrationWiring:
    def test_arbitration_used_on_empty_result(self, client: TestClient, monkeypatch) -> None:
        """F19：ASR 空结果 → 先仲裁；仲裁有结果则不报错、直接用仲裁文本判分。"""
        uid, _ = _make_student(client)
        fs = FakeSession(
            uid,
            mock_extra={"behavior": "empty"},
            top_extra={"arbitrator": {
                "enabled": True, "model": "qwen3.5-omni-plus",
                "api_key": "sk-x", "endpoint": "https://example.invalid",
            }},
        )

        async def fake_call(payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
            return {"output": {"choices": [{"message": {"content": "classroom"}}]}}

        fs.session.arbitrator._call = fake_call        # type: ignore[method-assign]
        assert fs.session.arbitrator.should_trigger("empty") is True

        async def flow() -> None:
            await fs.start()
            await fs.audio(3)
            await fs.end()

        run(flow())
        assert fs.last("error") is None, "仲裁救回来了就不该再报错"
        verdict = fs.last("verdict")
        assert verdict is not None and verdict["asr_text"] == "classroom"

    def test_arbitration_not_used_on_correct_answer(self, client: TestClient) -> None:
        """严禁进主链路：判定正确时**绝不**调用仲裁。"""
        uid, _ = _make_student(client)
        fs = FakeSession(uid, top_extra={"arbitrator": {
            "enabled": True, "model": "qwen3.5-omni-plus",
            "api_key": "sk-x", "endpoint": "https://example.invalid",
        }})
        called = {"n": 0}

        async def fake_call(payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
            called["n"] += 1
            return {"output": {"choices": [{"message": {"content": "x"}}]}}

        fs.session.arbitrator._call = fake_call        # type: ignore[method-assign]

        async def flow() -> None:
            await fs.start()
            await fs.audio(4)
            await fs.end()

        run(flow())
        assert fs.last("verdict")["is_correct"] is True
        assert called["n"] == 0, "主链路不得触发仲裁（延迟秒级、按 token 计费）"


# ===========================================================================
# U1：真端点握手（走真 ASGI 握手流程）
# ===========================================================================

class TestEndpointHandshake:
    # 注：原 `test_invalid_token_*` / `test_missing_token_*` 断言无效/缺失 token →
    # 关闭码 4001，依赖已删除的 WS 鉴权（WP-F 拆鉴权）。简化版 WS 不再校验 token，故移除。

    def test_valid_token_runs_full_flow_over_websocket(self, client: TestClient) -> None:
        """真端点跑通主流程（真握手 + 真 ASGI 收发帧）。"""
        uid, token = _make_student(client)
        sid = _seed_session(uid)
        with client.websocket_connect(f"/ws/asr?token={token}") as ws:
            ws.send_text(json.dumps({
                "type": "start", "session_id": sid, "task_type": "word",
                "lang_hint": "en", "word_id": 1, "expected": "classroom",
            }))
            seen: list[dict[str, Any]] = []
            ws.send_bytes(CHUNK)
            ws.send_bytes(CHUNK)
            ws.send_bytes(CHUNK)
            ws.send_bytes(CHUNK)
            ws.send_text(json.dumps({"type": "end"}))
            while True:
                msg = json.loads(ws.receive_text())
                seen.append(msg)
                if msg["type"] == "verdict":
                    break
        kinds = [m["type"] for m in seen]
        assert kinds[0] == "started", kinds
        assert "partial" in kinds and "final" in kinds
        assert seen[-1]["is_correct"] is True
        assert seen[-1]["expected"] == "classroom"

    def test_heartbeat_frame_answered_over_websocket(self, client: TestClient) -> None:
        _, token = _make_student(client)
        with client.websocket_connect(f"/ws/asr?token={token}") as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            assert json.loads(ws.receive_text())["type"] == "pong"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _logs(client: TestClient) -> list[dict[str, Any]]:
    with read_conn() as conn:
        rows = conn.execute("SELECT * FROM asr_logs ORDER BY id").fetchall()
    return [dict(r) for r in rows]
