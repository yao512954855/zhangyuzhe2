"""QA-01 功能矩阵补验 —— 只补**没有现成真机证据**的那几条。

《40-集成与验收规格》§5.1 的 40 条里，绝大多数由各包 pytest / 真机烟测覆盖
（见 `qa/reports/2026-09-25.md` 的证据列）。本脚本**只补查下来确实缺证据的**：

    G1 (#1 / #7) 建不同年级的内容 → 学生端按年级筛选正确，多年级互不干扰
    G2 (#7)      管理端单元列表按 年级 + 学期 过滤，跨年级不串
    G3 (#24)     记录全字段落库：`retry_count` / `hint_level` / `used_keyboard` / `first_voice_ms`
    G4 (#36)     局域网部署交付物齐全（手册 + 服务注册脚本 + 备份脚本）
    G5 (#40)     二期功能（SRS / 大屏 / 家长端）在源码里**没有实现**

⚠️ **为什么 G1/G2 不能只靠 pytest**（读源码确认过，不是想当然）：
    `test_be03_content.py::test_admin_word_filters` 验的是 `keyword` / `status`，
    `test_student_only_filters` 验的是 `only=wrong` / `only=unmastered` ——
    **都不含年级维度**。而矩阵 #1 的验收方式明写「建不同年级内容，学生端按年级筛选正确」。
    这正是「逐条验收」才能发现的口径缺口：**有测试 ≠ 测过那条需求**。

跑法（用 gaps.sh；一般不用直接调）：

    env -u PYTHONPATH bash qa/perf/gaps.sh
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WORDS_HEADERS = [
    "学科", "年级", "学期", "单元名", "单元序号", "类型", "英文",
    "中文", "可接受答案", "音标", "词性", "例句英", "例句中", "是否拼读",
]

ADMIN_USER = "admin"
ADMIN_PASSWORD = "Smoke-Qa01-Pass"

RESULTS: list[tuple[str, bool, str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail, False))
    print(f"[{'OK' if ok else '!!'}] #{len(RESULTS):02d} {name}    {detail}", flush=True)
    return bool(ok)


def skip(name: str, reason: str) -> None:
    RESULTS.append((name, True, reason, True))
    print(f"[--] SKIP {name}    {reason}", flush=True)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _xlsx(headers: list[str], rows: list[list]) -> bytes:
    from app.core.excel import write_xlsx

    return write_xlsx(headers, rows)


def _rows(db: Path, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _word_row(en: str, cn: str, *, unit: str, grade: int, semester: str) -> list:
    """一行词条。列序必须与 `WORDS_HEADERS` 一致。"""
    return ["英语", grade, semester, unit, 1, "单词", en, cn, "", "", "", "", "", "是"]


def import_words(
    c: httpx.Client, base: str, ah: dict, *, unit: str, grade: int, semester: str, rows: list
) -> dict:
    """两步式导入（preview → commit），返回 commit 响应。"""
    content = _xlsx(WORDS_HEADERS, rows)
    pv = c.post(
        base + "/api/import/words/preview",
        files={"file": ("words.xlsx", content, XLSX_MIME)},
        headers=ah,
    ).json()
    if pv.get("code") != 0:
        return pv
    return c.post(
        base + "/api/import/words/commit",
        json={
            "preview_token": pv["data"]["preview_token"],
            "create_missing_units": True,
            "on_duplicate": "overwrite",
        },
        headers=ah,
    ).json()


def my_units(c: httpx.Client, base: str, sh: dict) -> list[str]:
    body = c.get(base + "/api/me/units", headers=sh).json()
    return [str(u.get("name")) for u in ((body.get("data") or {}).get("items") or [])]


def set_pref(c: httpx.Client, base: str, sh: dict, grade: int, semester: str = "first") -> dict:
    return c.put(
        base + "/api/me/profile",
        json={"preferred_grade": grade, "preferred_semester": semester},
        headers=sh,
    ).json()


# ---------------------------------------------------------------------------
# G1 / G2：年级维度隔离
# ---------------------------------------------------------------------------


def g1_g2_grade_isolation(c: httpx.Client, base: str, ah: dict, sh: dict) -> None:
    u3, u4 = "Unit 3 Apple", "Unit 4 Robot"
    r3 = import_words(
        c, base, ah, unit=u3, grade=3, semester="上学期",
        rows=[_word_row("apple", "苹果", unit=u3, grade=3, semester="上学期"),
              _word_row("banana", "香蕉", unit=u3, grade=3, semester="上学期")],
    )
    r4 = import_words(
        c, base, ah, unit=u4, grade=4, semester="上学期",
        rows=[_word_row("robot", "机器人", unit=u4, grade=4, semester="上学期"),
              _word_row("rocket", "火箭", unit=u4, grade=4, semester="上学期")],
    )
    check(
        "G1 前置：3 年级 / 4 年级各导入 1 个单元（同一份逻辑，跨年级文件）",
        r3.get("code") == 0 and r4.get("code") == 0,
        f"g3_created={(r3.get('data') or {}).get('created')} g4_created={(r4.get('data') or {}).get('created')}",
    )

    set_pref(c, base, sh, 3)
    names3 = my_units(c, base, sh)
    check(
        "G1 学生偏好 3 年级：只看到 3 年级单元（4 年级不串进来）",
        u3 in names3 and u4 not in names3,
        f"units={names3}",
    )

    set_pref(c, base, sh, 4)
    names4 = my_units(c, base, sh)
    check(
        "G1 切到 4 年级：只看到 4 年级单元（筛选随偏好即时生效）",
        u4 in names4 and u3 not in names4,
        f"units={names4}",
    )

    # G2 管理端：按 年级 + 学期 过滤
    a3 = c.get(base + "/api/units", params={"subject": "english", "grade": 3, "semester": "first"}, headers=ah).json()
    n3 = [str(u.get("name")) for u in ((a3.get("data") or {}).get("items") or [])]
    check(
        "G2 管理端 /api/units?grade=3 只返回 3 年级单元",
        u3 in n3 and u4 not in n3,
        f"units={n3}",
    )


# ---------------------------------------------------------------------------
# G3：记录全字段落库
# ---------------------------------------------------------------------------


def g3_answer_fields(c: httpx.Client, base: str, sh: dict, db: Path) -> None:
    # ⚠️ 先把偏好切回 3 年级：G1 的最后一步把偏好留在了 4 年级，
    #    而 `/api/me/units` 是**按偏好过滤**的 —— 不切回来就会「找不到 3 年级单元」。
    #    （首跑就踩了这条，症状是前置失败而不是断言失败。）
    set_pref(c, base, sh, 3)

    units = c.get(base + "/api/me/units", headers=sh).json()
    items = (units.get("data") or {}).get("items") or []
    # ⚠️ `/api/me/units` 的列表项主键字段叫 **`unit_id`**（不是 `id`）——
    #    它是 `me_learning.py` 里手工拼的字典，不走 `UnitOut`。
    #    这里两种都认，避免以后又因为字段名对不上白跑一轮。
    unit_id = next(
        (
            int(u.get("unit_id") or u.get("id") or 0)
            for u in items
            if u.get("name") == "Unit 3 Apple"
        ),
        None,
    )
    if unit_id is None:
        check("G3 记录全字段落库（重试 / 提示 / 键盘降级 / 首次发声延迟）", False, "找不到 3 年级单元，前置失败")
        return

    sess = c.post(
        base + "/api/sessions",
        json={
            "unit_id": unit_id,
            "content_type": "english",
            "strategy": {"count": 2, "order": "sequential", "mode": "cn2en"},
        },
        headers=sh,
    ).json()
    sess_items = (sess.get("data") or {}).get("items") or []
    if sess.get("code") != 0 or not sess_items:
        check("G3 记录全字段落库（重试 / 提示 / 键盘降级 / 首次发声延迟）", False, f"组卷失败：{sess}")
        return
    session_id = int(sess["data"]["session_id"])
    word_id = int(sess_items[0]["word_id"])

    ans = c.post(
        base + f"/api/sessions/{session_id}/answers",
        json={
            "word_id": word_id,
            "mode": "cn2en",
            "step": 1,
            "asr_text": "applo",
            "duration_ms": 2400,
            "first_voice_ms": 640,
            "retry_count": 2,
            "used_keyboard": True,
            "hint_level": 2,
            "used_hint_count": 1,
            "provider": "mock",
        },
        headers={**sh, "Idempotency-Key": f"gap-{session_id}-{word_id}"},
    ).json()

    rows = _rows(db, "SELECT * FROM answers WHERE session_id = ?", (session_id,))
    row = rows[0] if rows else {}
    check(
        "G3 记录全字段落库（重试 / 提示 / 键盘降级 / 首次发声延迟）",
        ans.get("code") == 0
        and len(rows) == 1
        and int(row.get("retry_count") or 0) == 2
        and int(row.get("hint_level") or 0) == 2
        and int(row.get("used_hint_count") or 0) == 1
        and int(row.get("used_keyboard") or 0) == 1
        and int(row.get("first_voice_ms") or 0) == 640
        and int(row.get("duration_ms") or 0) == 2400
        and str(row.get("provider")) == "mock",
        f"retry={row.get('retry_count')} hint_level={row.get('hint_level')} "
        f"hint_cnt={row.get('used_hint_count')} keyboard={row.get('used_keyboard')} "
        f"first_voice={row.get('first_voice_ms')} duration={row.get('duration_ms')}",
    )


# ---------------------------------------------------------------------------
# G4 / G5：纯静态检查（不需要后端，但放在同一份报告里）
# ---------------------------------------------------------------------------


def g4_deploy_artifacts() -> None:
    need = {
        "ops/README.md": "部署与运维手册（含 §3 固定 IP、§5 服务化、§6 备份恢复）",
        "ops/install_service.ps1": "Windows 服务注册（开机自启）",
        "ops/uninstall_service.ps1": "服务卸载",
        "ops/backup.bat": "每日备份任务的可执行脚本",
    }
    missing = [p for p in need if not (ROOT / p).is_file()]
    check(
        "G4 局域网部署交付物齐全（手册 + 服务注册 + 备份脚本）",
        not missing,
        f"缺={missing}" if missing else f"齐 {len(need)} 件：" + "、".join(need),
    )

    readme = (ROOT / "ops/README.md").read_text(encoding="utf-8", errors="ignore")
    has_ip = "固定 IP" in readme or "固定IP" in readme
    has_restore = "恢复" in readme and "备份" in readme
    check(
        "G4 手册含「固定 IP」与「备份/恢复」章节（可照做）",
        has_ip and has_restore,
        f"固定IP章节={has_ip} 备份恢复章节={has_restore}",
    )
    skip(
        "G4 按手册实际部署一遍（真机 + 真路由器 + 真手机）",
        "需要真实局域网环境与至少 2 台手机；手册已交付，执行属上线前人工项",
    )


#: 二期功能关键词。⚠️ 不能用 `srs` 裸搜 —— 会命中 `ASRSession`（A-**SRS**-ession）造成假阳性。
PHASE2_KEYWORDS = {
    "间隔重复 / SRS 算法": ("间隔重复", "遗忘曲线", "SM-2", "spaced_repetition"),
    "大屏展示": ("大屏", "投屏", "电子白板"),
    "家长端": ("家长端", "parent_app", "父端"),
}


def g5_no_phase2_impl() -> None:
    targets = [ROOT / "backend/app", ROOT / "admin-web/src", ROOT / "student-app/lib"]
    hits: dict[str, list[str]] = {}
    for label, words in PHASE2_KEYWORDS.items():
        found: list[str] = []
        for root in targets:
            if not root.is_dir():
                continue
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in {".py", ".ts", ".vue", ".dart", ".js"}:
                    continue
                try:
                    body = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    continue
                for w in words:
                    if w in body:
                        found.append(f"{p.relative_to(ROOT)} ({w})")
        if found:
            hits[label] = found
    check(
        "G5 二期功能（SRS / 大屏 / 家长端）在源码中无实现，仅预留",
        not hits,
        f"命中 {sum(len(v) for v in hits.values())} 处" + (f"：{hits}" if hits else ""),
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--min-checks", type=int, default=6)
    args = ap.parse_args()

    base = args.base.rstrip("/")
    db = Path(args.db)
    c = httpx.Client(timeout=20, trust_env=False)

    login = c.post(
        base + "/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "role": "admin"},
    ).json()
    if login.get("code") != 0:
        print(f"❌ admin 登录失败：{login}", flush=True)
        return 1
    ah = {"Authorization": f"Bearer {login['data']['access_token']}"}

    stu_user, stu_pwd = "20260301", "gap123456"
    c.post(base + "/api/auth/register", json={"username": stu_user, "real_name": "补验学生", "password": stu_pwd})
    stu = c.post(base + "/api/auth/login", json={"username": stu_user, "password": stu_pwd, "role": "student"}).json()
    if stu.get("code") != 0:
        print(f"❌ 学生登录失败：{stu}", flush=True)
        return 1
    sh = {"Authorization": f"Bearer {stu['data']['access_token']}"}

    print("\n== G1 / G2 年级维度隔离 ==", flush=True)
    g1_g2_grade_isolation(c, base, ah, sh)

    print("\n== G3 记录全字段落库 ==", flush=True)
    g3_answer_fields(c, base, sh, db)

    print("\n== G4 部署交付物 ==", flush=True)
    g4_deploy_artifacts()

    print("\n== G5 二期无实现 ==", flush=True)
    g5_no_phase2_impl()

    c.close()

    passed = sum(1 for _, ok, _, sk in RESULTS if ok and not sk)
    skipped = sum(1 for _, _, _, sk in RESULTS if sk)
    failed = [r for r in RESULTS if not r[1] and not r[3]]
    print()
    for name, _, detail, _ in failed:
        print(f"  未通过：{name}    {detail}")
    print(f"SUMMARY pass={passed} total={passed + len(failed)} skipped={skipped} failed={len(failed)}")

    if passed < args.min_checks:
        print(f"❌ 真断言数 {passed} 低于门限 {args.min_checks}")
        return 1
    if failed:
        print("❌ QA-01 缺口补验 未通过")
        return 1
    print("✅ QA-01 缺口补验 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
