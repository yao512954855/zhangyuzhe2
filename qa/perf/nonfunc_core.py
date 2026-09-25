"""QA-01 非功能验收 —— 真进程 + 真 TCP + 真 WebSocket（Mock ASR）。

覆盖《40-集成与验收规格》§5.2 非功能验收里**可自动化**的项：

    NF-01 接口响应：常规查询 P95 < 300ms（局域网）
    NF-02 判分延迟：服务端判定 < 100ms
    NF-05 WS 上限：第 61 路被正确拒绝并给出友好提示
    NF-07 安全：错误信息不泄露内部路径；前端无硬编码密钥

**不可自动化的项如实 `skip()`**（单独计数、不冒充通过）：

    NF-03 实时识别延迟 < 1.5s
          —— 规格要求「人工计时×10」。Mock ASR 没有真实云端往返，
             在这里测出来的数字**只反映本机 mock 的速度**，拿它当验收证据是假绿。
    NF-09 连续 7 天备份成功
          —— 需要真实日历时间。已在 `qa/smoke/smoke_core.py` #39 与
             `qa/e2e/e2e_core.py` 的 E2E-06 验到「单次备份 + 真恢复」。

与既有产物的分工：
    `qa/smoke/smoke.sh`  验「核心链路能不能跑通」（功能）
    `qa/e2e/e2e.sh`      验「10 个端到端场景」（功能）
    **本脚本**           验「跑得够不够快、超限会不会优雅拒绝、报错会不会漏内部信息」（非功能）

⚠️ 关于 WS 上限的口径（**实测才发现，与规格字面不同**）：
    `40-规格 §5.2` 写的是「**第 61 路被正确拒绝**并给出友好提示」，
    而服务端实现是 **60 路并发 + 120 路排队**（`session_manager.py:51-53`），
    排队超过 `DEFAULT_QUEUE_WAIT_MS = 5000` 才拒。
    所以第 61 路**不会**被立即拒绝，而是**进排队、等满 5 秒**才收到 `50001`。
    本脚本按**实现**断言（进排队 → 5s 后 50001 + `action=abort`），
    并把这条口径差异写进验收报告，而不是把脚本改成迎合文档字面。

跑法（一般不用直接调，用 `nonfunc.sh`）：

    env -u PYTHONPATH bash qa/perf/nonfunc.sh
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

ADMIN_USER = "admin"
ADMIN_PASSWORD = "Smoke-Qa01-Pass"
STUDENT_USER = "20260101"
STUDENT_PASSWORD = "student123"

#: 单接口采样次数。300 次 × 6 个接口 ≈ 1800 请求，在局域网下几秒内跑完。
SAMPLES_PER_ENDPOINT = 300
#: 判分函数采样次数（纯 CPU，多跑些才有统计意义）
JUDGE_SAMPLES = 3000
#: WS 并发路数（= 服务端 `DEFAULT_MAX_CONNECTIONS`）
WS_CONCURRENCY = 60

RESULTS: list[tuple[str, bool, str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    """登记一条**真断言**。"""
    RESULTS.append((name, bool(ok), detail, False))
    print(f"[{'OK' if ok else '!!'}] #{len(RESULTS):02d} {name}    {detail}", flush=True)
    return bool(ok)


def skip(name: str, reason: str) -> None:
    """无法自动化的项 —— 单独计数，绝不冒充通过。"""
    RESULTS.append((name, True, reason, True))
    print(f"[--] SKIP {name}    {reason}", flush=True)


def pct(values: list[float], q: float) -> float:
    """百分位（最近邻法，够用且不会因插值产生「比 max 还大」的怪值）。"""
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q / 100.0 * len(ordered))) - 1))
    return ordered[idx]


# ---------------------------------------------------------------------------
# NF-01 接口响应 P95
# ---------------------------------------------------------------------------


def nf01_latency(c: httpx.Client, base: str, admin_h: dict, student_h: dict) -> None:
    endpoints: list[tuple[str, str, dict]] = [
        ("GET /api/health（免鉴权）", "/api/health", {}),
        ("GET /api/ping（免鉴权）", "/api/ping", {}),
        ("GET /api/server/info（免鉴权）", "/api/server/info", {}),
        ("GET /api/classes（管理端）", "/api/classes", admin_h),
        ("GET /api/me/units（学生端）", "/api/me/units", student_h),
    ]

    for label, path, headers in endpoints:
        # 预热：排除首次建连 TLS/TCP 握手与懒加载开销
        for _ in range(5):
            c.get(base + path, headers=headers)

        samples: list[float] = []
        bad = 0
        for _ in range(SAMPLES_PER_ENDPOINT):
            t0 = time.perf_counter()
            r = c.get(base + path, headers=headers)
            samples.append((time.perf_counter() - t0) * 1000.0)
            if r.status_code != 200:
                bad += 1

        p50, p95, mx = pct(samples, 50), pct(samples, 95), max(samples)
        check(
            f"NF-01 {label} P95 < 300ms",
            p95 < 300.0 and bad == 0,
            f"n={SAMPLES_PER_ENDPOINT} P50={p50:.1f}ms P95={p95:.1f}ms max={mx:.1f}ms 非200={bad}",
        )


# ---------------------------------------------------------------------------
# NF-02 判分延迟
# ---------------------------------------------------------------------------


def nf02_judge_latency() -> None:
    from app.services.judge.service import JudgeService

    cases_text = [
        ("CLASS", "class"),
        ("glass", "class"),
        ("CLAS", "class"),
        ("我读对了", "我读对了"),
    ]
    cases_spell = [
        ("c l a s s", "class"),
        ("c l a s", "class"),
        ("s e v e n", "seven"),
    ]

    def sample(fn, cases: list[tuple[str, str]]) -> tuple[list[float], int]:
        times: list[float] = []
        empty = 0
        for i in range(JUDGE_SAMPLES):
            a, e = cases[i % len(cases)]
            t0 = time.perf_counter()
            v = fn(answer=a, expected=e) if fn is JudgeService.judge_text else fn(
                asr_text=a, target=e
            )
            times.append((time.perf_counter() - t0) * 1000.0)
            if v is None:
                empty += 1
        return times, empty

    # 预热（避免第一次调用把 import/懒初始化算进去）
    JudgeService.judge_text(answer="class", expected="class")
    JudgeService.judge_spell(asr_text="c l a s s", target="class")

    t_text, _ = sample(JudgeService.judge_text, cases_text)
    t_spell, _ = sample(JudgeService.judge_spell, cases_spell)

    for label, times in (("说词 / 英译中（judge_text）", t_text), ("拼读（judge_spell）", t_spell)):
        p95 = pct(times, 95)
        check(
            f"NF-02 {label} P95 < 100ms",
            p95 < 100.0,
            f"n={JUDGE_SAMPLES} P50={pct(times, 50):.3f}ms P95={p95:.3f}ms max={max(times):.3f}ms",
        )


# ---------------------------------------------------------------------------
# NF-05 WS 并发上限
# ---------------------------------------------------------------------------


async def _drain_until_closed(ws: Any, timeout: float = 3.0) -> tuple[int | None, list[str]]:
    """读到对端关闭为止，返回 (关闭码, 已收到的帧摘要)。超时未关闭则关闭码为 None。"""
    import websockets

    deadline = time.monotonic() + timeout
    seen: list[str] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, seen
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            seen.append(
                (raw if isinstance(raw, str) else f"<bin {len(raw)}>")[:60]
            )
        except websockets.exceptions.ConnectionClosed as exc:
            return exc.code, seen
        except asyncio.TimeoutError:
            return None, seen


async def nf05_ws_limit(ws_base: str, tokens: list[str]) -> None:
    """NF-05：全局并发闸门 + 同账号踢旧连接。

    ⚠️ **首跑写错过一次，这里记下来免得再踩**：
        第一版用**同一个学生的 token** 连了 60 路，结果第 61 路**被直接接受**，
        看起来像「闸门不生效」。实际原因是 `SessionRegistry.register()` 对同账号
        做了「踢旧连接」（U11 / F12）：新连接把旧连接关掉，旧连接在 `finally` 里
        `await gate.release()`，于是并发位**始终只占 1~2 个**。
        **结论：测全局闸门必须用 N 个不同账号**，否则测的是 U11 而不是 F12。
    """
    import websockets

    def url_of(tok: str) -> str:
        return f"{ws_base}?token={tok}"

    # ---- A) 同账号踢旧连接（U11）——顺便把上面那个坑钉成一条断言 ----
    w1 = await websockets.connect(url_of(tokens[0]), max_size=None, open_timeout=10)
    w2 = await websockets.connect(url_of(tokens[0]), max_size=None, open_timeout=10)
    code1, _ = await _drain_until_closed(w1, timeout=3.0)
    try:
        await w2.close()
    except Exception:  # noqa: BLE001
        pass
    check(
        "NF-05 同账号第 2 路建立后，第 1 路被服务端踢下线（U11，不占并发位）",
        code1 is not None,
        f"旧连接关闭码={code1}",
    )

    # ---- B) 全局并发闸门：**60 个不同账号** ----
    conns: list[Any] = []
    open_errors: list[str] = []
    for tok in tokens[:WS_CONCURRENCY]:
        try:
            conns.append(await websockets.connect(url_of(tok), max_size=None, open_timeout=10))
        except Exception as exc:  # noqa: BLE001
            open_errors.append(f"{type(exc).__name__}: {exc}")

    check(
        f"NF-05 前 {WS_CONCURRENCY} 路并发（不同账号）全部握手成功",
        len(conns) == WS_CONCURRENCY,
        f"成功={len(conns)}/{WS_CONCURRENCY} errors={open_errors[:3]}",
    )

    # 第 61 路（又一个新账号）：实现是「进排队 → 5s 超时 → 50001 + action=abort」
    over: dict[str, Any]
    t0 = time.perf_counter()
    try:
        ws61 = await websockets.connect(url_of(tokens[WS_CONCURRENCY]), max_size=None, open_timeout=10)
        try:
            raw = await asyncio.wait_for(ws61.recv(), timeout=8.0)
            over = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except asyncio.TimeoutError:
            over = {"type": "__no_frame_within_8s__"}
        except websockets.exceptions.ConnectionClosed as exc:
            over = {"type": "__closed__", "code": exc.code, "reason": exc.reason}
        try:
            await ws61.close()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        over = {"type": "__connect_failed__", "err": f"{type(exc).__name__}: {exc}"}
    waited = time.perf_counter() - t0

    check(
        "NF-05 第 61 路被拒并给出 50001（排队超时）",
        over.get("code") == 50001,
        f"实际={json.dumps(over, ensure_ascii=False)[:160]} 等待={waited:.1f}s",
    )
    check(
        "NF-05 拒绝时带友好提示（message + action=abort）",
        bool(over.get("action")) and bool(str(over.get("message") or "").strip()),
        f"message={str(over.get('message'))[:40]!r} action={over.get('action')!r}",
    )
    check(
        "NF-05 第 61 路是**排队等待**而非立即拒绝（等待 ≈ 服务端 queue_wait_ms=5000）",
        waited >= 4.0,
        f"实测等待={waited:.1f}s（<4s 说明实现改成了立即拒，需同步修订报告口径）",
    )

    # 释放并发位后应能重新接入（证明计数器归还正常，不是泄漏成永久拒绝）
    for ws in conns:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
    await asyncio.sleep(0.8)
    try:
        ws_re = await websockets.connect(url_of(tokens[0]), max_size=None, open_timeout=10)
        await ws_re.close()
        recovered = True
        detail = f"释放 {WS_CONCURRENCY} 路后可重新接入"
    except Exception as exc:  # noqa: BLE001
        recovered = False
        detail = f"{type(exc).__name__}: {exc}"
    check("NF-05 释放并发位后可重新接入（计数归还正常）", recovered, detail)


# ---------------------------------------------------------------------------
# NF-07 安全：不泄露内部路径 / 前端无密钥
# ---------------------------------------------------------------------------

#: 泄露特征：Python 堆栈、本机绝对路径、DB 文件名、源码文件名
LEAK_PATTERNS = (
    "/d/helloagent",
    "/home/",
    "Traceback",
    'File "',
    ".py\", line",
    "sqlite3",
    "OperationalError",
    ".db",
)


def nf07_security(c: httpx.Client, base: str, admin_h: dict, student_h: dict) -> None:
    probes: list[tuple[str, str, dict, dict | None]] = [
        ("GET 不存在的班级凭据", "GET", "/api/classes/999999/students/credentials", admin_h, None),
        ("POST 不存在的场次答题", "POST", "/api/sessions/999999/answers", student_h,
         {"word_id": 1, "answer": "x", "task_type": "word"}),
        ("GET 不存在的词条", "GET", "/api/words/999999", admin_h, None),
    ]

    leaked: list[str] = []
    for label, method, path, headers, body in probes:
        r = c.request(method, base + path, headers=headers, json=body)
        text = r.text
        hits = [p for p in LEAK_PATTERNS if p in text]
        if hits:
            leaked.append(f"{label}: {hits} | {text[:120]}")
    check(
        "NF-07 错误响应不泄露内部路径 / 堆栈 / DB 文件名",
        not leaked,
        f"探测 {len(probes)} 条，泄露 {len(leaked)} 条" + (f" 例：{leaked[0][:150]}" if leaked else ""),
    )

    # 前端源码不得出现硬编码密钥（构建产物已在设计上不含 AccessKey —— 客户端从不持有）
    secret_patterns = ("sk-", "LTAI", "AKID", "AccessKeySecret", "access_key_secret")
    hits2: list[str] = []
    for root_name in ("admin-web/src", "student-app/lib"):
        root = ROOT / root_name
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in {".ts", ".vue", ".dart", ".js"}:
                continue
            try:
                body = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            for pat in secret_patterns:
                if pat in body:
                    hits2.append(f"{p.relative_to(ROOT)}: {pat}")
    check(
        "NF-07 前端源码无硬编码密钥（sk-/LTAI/AKID/AccessKeySecret）",
        not hits2,
        f"命中 {len(hits2)} 处" + (f" 例：{hits2[:3]}" if hits2 else ""),
    )


def nf07_rate_limit(c: httpx.Client, base: str) -> None:
    """限流真的生效（防暴力破解）+ 登录失败不泄露用户是否存在。

    两条用**不同 ident** 分别验，互不干扰：

    A) 带一个**唯一且固定**的 `Authorization` 头 → `_identity()` 按该头哈希建桶，
       拿到全新额度，于是可以**精确断言**「前 5 次放行、第 6 次起 10005」
       （契约 §5.4 的档位就是「单账号登录 5 次/分钟」）。

    B) **不带** `Authorization` → `_identity()` 退化成整个来源 IP（`rate_limit.py:96-103`）。
       此时额度可能已被本脚本先前的登录消耗掉一部分，故只断言「7 次必出 10005」——
       无论 IP 桶剩 0~5 个额度，7 次里至少有 2 次会被拒。

    ⚠️ 首跑踩的坑（记录以免重犯）：payload **漏了 `role`**（`LoginIn.role` 是**必填**），
       Pydantic 直接报 `10001`，看起来像「限流没生效」，其实是被参数校验挡在门外。
       （上一轮曾误记成「用户名含下划线导致格式非法」—— **不是**：
       `clean_username` 只做全角→半角与去空格，不校验字符集。）
    """
    payload = {"username": "nosuchstudent9", "password": "WrongPass123", "role": "student"}

    # A) 独立 ident → 精确验证「5 次额度」
    probe = {"Authorization": "Bearer nf-ratelimit-probe-ident"}
    codes: list[int] = []
    for _ in range(7):
        r = c.post(base + "/api/auth/login", headers=probe, json=payload).json()
        codes.append(int(r.get("code") or -1))
    check(
        "NF-07 登录限流生效（同 ident 第 6 次起 → 10005「操作太频繁了」）",
        codes[:5] == [20002] * 5 and codes[5:] == [10005] * 2,
        f"codes={codes}（前 5 次业务判失败，第 6/7 次被限流）",
    )
    check(
        "NF-07 登录失败不泄露用户是否存在（不存在也回 20002，非 20001）",
        bool(codes) and codes[0] == 20002,
        f"首答 code={codes[0] if codes else None}（20002 = 用户名或密码错误，不区分「用户不存在」）",
    )

    # B) 无鉴权头 → 退回 IP 桶（退化行为另见报告 §5.6）
    ip_codes: list[int] = []
    for _ in range(7):
        r = c.post(base + "/api/auth/login", json=payload).json()
        ip_codes.append(int(r.get("code") or -1))
    check(
        "NF-07 无鉴权头时按来源 IP 计数同样限流",
        10005 in ip_codes,
        f"codes={ip_codes}（不带 Authorization → _identity 退化到 ip:来源）",
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def make_student_tokens(c: httpx.Client, base: str, n: int) -> list[str]:
    """注册并登录 n 个**不同**学生账号，返回 token 列表。

    ⚠️ 两个坑，都是首跑踩出来的：

    1) **必须不同账号**：同账号开多路会被 U11「踢旧连接」处理掉，
       并发位始终只占 1~2 个 —— 那是测不到全局闸门的（见 `nf05_ws_limit` 注释）。

    2) **注册/登录请求要各带一个唯一的 `Authorization` 头**：
       `RateLimitMiddleware._identity()` 的主体是「有 `Authorization` 就按它的哈希，
       **没有就退化成整个 IP**」。注册/登录本来就没有鉴权头，于是 61 个账号的
       注册 + 登录**全挤在 `ip:127.0.0.1` 同一个桶**里（`LOGIN_LIMIT=5` / 分钟、
       `WRITE_LIMIT=60` / 分钟）→ 首跑实测只备好 **3/61** 就被 `10005` 挡住。
       给每个请求一个唯一的头 = 每个请求各自一个桶。**不改产品代码**，
       也不掩盖限流本身 —— 限流真的生效由 `nf07_rate_limit` 单独断言。
    """
    tokens: list[str] = []
    fails: list[str] = []
    for i in range(1, n + 1):
        u = f"nf{i:04d}"
        probe = {"Authorization": f"Bearer probe-{i}"}
        reg = c.post(
            base + "/api/auth/register",
            json={"username": u, "real_name": f"非功能{i}", "password": "nf123456"},
            headers=probe,
        ).json()
        r = c.post(
            base + "/api/auth/login",
            json={"username": u, "password": "nf123456", "role": "student"},
            headers=probe,
        ).json()
        if r.get("code") == 0:
            tokens.append(r["data"]["access_token"])
        elif len(fails) < 5:
            fails.append(
                f"{u}: reg={reg.get('code')}/{str(reg.get('message'))[:24]} "
                f"login={r.get('code')}/{str(r.get('message'))[:24]}"
            )
    if fails:
        print("  ⚠️ 注册/登录失败样例：" + " | ".join(fails), flush=True)
    return tokens


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--ws-base", required=True)
    ap.add_argument("--min-checks", type=int, default=10)
    args = ap.parse_args()

    base = args.base.rstrip("/")
    ws_base = args.ws_base.rstrip("/") + "/ws/asr"
    c = httpx.Client(timeout=20, trust_env=False)

    # ---- 账号准备（admin 由首次启动自动建）----
    login = c.post(
        base + "/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "role": "admin"},
    ).json()
    if login.get("code") != 0:
        print(f"❌ admin 登录失败，后续无法进行：{login}", flush=True)
        return 1
    admin_h = {"Authorization": f"Bearer {login['data']['access_token']}"}

    c.post(
        base + "/api/auth/register",
        json={"username": STUDENT_USER, "real_name": "非功能学生", "password": STUDENT_PASSWORD},
    )
    stu = c.post(
        base + "/api/auth/login",
        json={"username": STUDENT_USER, "password": STUDENT_PASSWORD, "role": "student"},
    ).json()
    if stu.get("code") != 0:
        print(f"❌ 学生登录失败：{stu}", flush=True)
        return 1
    student_h = {"Authorization": f"Bearer {stu['data']['access_token']}"}

    print("\n== NF-01 接口响应 P95 ==", flush=True)
    nf01_latency(c, base, admin_h, student_h)

    print("\n== NF-02 判分延迟 ==", flush=True)
    nf02_judge_latency()

    print(f"\n== NF-05 WebSocket 并发上限（需要 {WS_CONCURRENCY + 1} 个不同账号）==", flush=True)
    tokens = make_student_tokens(c, base, WS_CONCURRENCY + 1)
    if len(tokens) < WS_CONCURRENCY + 1:
        print(f"❌ 账号准备不足：{len(tokens)}/{WS_CONCURRENCY + 1}", flush=True)
        return 1
    print(f"  已备好 {len(tokens)} 个账号（不同 user_id，才能真的占到并发位）", flush=True)
    asyncio.run(nf05_ws_limit(ws_base, tokens))

    print("\n== NF-07 安全 ==", flush=True)
    nf07_security(c, base, admin_h, student_h)
    nf07_rate_limit(c, base)  # ⚠️ 必须最后跑：会把本 IP 的登录额度用光

    print("\n== 不可自动化项（如实标注）==", flush=True)
    skip(
        "NF-03 实时识别延迟 < 1.5s",
        "规格要求人工计时×10；Mock ASR 无真实云端往返，本机测得的数字不构成验收证据",
    )
    skip(
        "NF-09 连续 7 天备份成功且可恢复",
        "需要真实日历时间；单次备份 + 真停服换库恢复已由 smoke #39 与 E2E-06 覆盖",
    )
    c.close()

    passed = sum(1 for _, ok, _, sk in RESULTS if ok and not sk)
    skipped = sum(1 for _, _, _, sk in RESULTS if sk)
    failed = [r for r in RESULTS if not r[1] and not r[3]]

    print()
    for name, _, detail, _ in failed:
        print(f"  未通过：{name}    {detail}")
    print(f"SUMMARY pass={passed} total={passed + len(failed)} skipped={skipped} failed={len(failed)}")

    if passed < args.min_checks:
        print(f"❌ 真断言数 {passed} 低于门限 {args.min_checks}（用例被静默跳过？）")
        return 1
    if failed:
        print("❌ QA-01 非功能验收 未通过")
        return 1
    print("✅ QA-01 非功能验收 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
