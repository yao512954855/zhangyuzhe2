"""WP-BE-05 真机烟测 —— 语文背诵引擎（真 uvicorn + 真 TCP + 真 xlsx）。

与 `pytest` 的分工：pytest 用 `TestClient`（进程内直调 ASGI），
本脚本起**真 uvicorn**、走**真 TCP**，验「装起来之后到底能不能用」。

链路：管理端导入语文课文 → 学生注册 → 开语文场次 → 取课文详情 →
创建背诵 → 流式喂识别结果（累计语义 / 同音字 / 漏字 / 噪声）→
三档提示 → 重来 → 结算（**谎报数据验服务端重算**）→ 落库校验（含事件表）。

端口默认 **8899**（避开 8810 正式口、8897 BE-03、8898 BE-04），`SMOKE_PORT` 可改。

跑法：

    cd backend
    ./.venv/Scripts/python.exe scripts/smoke_be05.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import openpyxl

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
PORT = int(os.environ.get("SMOKE_PORT", "8899"))
BASE = f"http://127.0.0.1:{PORT}"

ADMIN_USER = "admin"
ADMIN_PASSWORD = "smoke-be05-Admin-Pass"
STUDENT_USER = "20260905"
STUDENT_PASSWORD = "Student-Pass-1"

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    ok = bool(ok)
    _results.append((ok, name, detail))
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail:
        line += f"  |  {detail}"
    print(line, flush=True)


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_ready(timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{BASE}/api/health", timeout=1.5)
            if resp.status_code == 200:
                return True
        except Exception:
            time.sleep(0.3)
    return False


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 造 Excel（与《10a》模板列一致）
# ---------------------------------------------------------------------------


def _texts_xlsx() -> bytes:
    """三篇课文：

    1. 《背影》2 段，只背第 1 段（**背诵范围不含末段**）
    2. 《春》1 段，全文
    3. 《荷塘月色》3 段，只背后 2 段（**背诵范围不含首段** —— 切片错位的回归用例）
    """
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "课文"
    sheet.append(["学科", "年级", "学期", "单元名", "课文标题", "作者", "段落正文", "背诵范围"])
    sheet.append(
        [
            "语文",
            "7",
            "上",
            "第一单元",
            "背影",
            "朱自清",
            "我与父亲不相见\n那年冬天",
            "0",
        ]
    )
    sheet.append(["语文", "7", "上", "第一单元", "春", "朱自清", "盼望着盼望着", ""])
    sheet.append(
        [
            "语文",
            "7",
            "上",
            "第一单元",
            "荷塘月色",
            "朱自清",
            "曲曲折折的荷塘上面\n弥望的是田田的叶子\n叶子出水很高",
            "1,2",
        ]
    )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _sql(db_file: Path, query: str, params: tuple = ()) -> list[dict]:
    """直连临时库做断言（只读打开，不干扰 uvicorn 的写连接）。"""
    path = Path(db_file)
    if not path.exists():
        raise RuntimeError(f"烟测库文件不存在：{path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _run_all(client: httpx.Client, db_file: Path) -> None:  # noqa: C901
    # ---- A. 管理员登录 ----
    body = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASSWORD, "role": "admin"},
    ).json()
    check("管理员登录", body["code"] == 0, f"code={body['code']}")
    admin_token = body["data"]["access_token"]

    # ---- B. Excel 导入语文课文 ----
    template = client.get("/api/import/template/texts", headers=_auth(admin_token))
    check(
        "下载语文课文模板",
        template.status_code == 200 and len(template.content) > 1000,
        f"status={template.status_code} bytes={len(template.content)}",
    )

    files = {
        "file": (
            "texts.xlsx",
            _texts_xlsx(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    preview = client.post(
        "/api/import/texts/preview", files=files, headers=_auth(admin_token)
    ).json()
    check(
        "课文 preview（解析校验）",
        preview["code"] == 0 and preview["data"]["summary"]["valid"] == 3,
        f"code={preview['code']} summary={preview['data'].get('summary') if preview['code'] == 0 else '-'}",
    )
    check(
        "preview 回传段落供前端勾选",
        preview["code"] == 0
        and preview["data"]["texts"][0]["paragraphs"][0]["text"].startswith("我与父亲"),
        "",
    )

    files = {
        "file": (
            "texts.xlsx",
            _texts_xlsx(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    preview2 = client.post(
        "/api/import/texts/preview", files=files, headers=_auth(admin_token)
    ).json()
    token = preview2["data"]["preview_token"]
    commit = client.post(
        "/api/import/texts/commit",
        json={"preview_token": token},
        headers=_auth(admin_token),
    ).json()
    check(
        "课文 commit（入库）",
        commit["code"] == 0 and commit["data"]["created"] == 3,
        f"code={commit['code']} created={commit['data'].get('created') if commit['code'] == 0 else '-'}",
    )

    # ---- C. 学生注册 ----
    registered = client.post(
        "/api/auth/register",
        json={"username": STUDENT_USER, "password": STUDENT_PASSWORD, "real_name": "烟测学生"},
    ).json()
    check("学生注册", registered["code"] == 0, f"code={registered['code']}")
    student_token = registered["data"]["access_token"]

    # ---- D. 语文场次（BE-05 补齐的链路）----
    rows = _sql(db_file, "SELECT id, title FROM texts ORDER BY order_no, id")
    check("库里有三篇课文", len(rows) == 3, f"n={len(rows)}")
    unit_id = _sql(db_file, "SELECT unit_id FROM texts ORDER BY id LIMIT 1")[0]["unit_id"]
    text_id = rows[0]["id"]

    session_body = client.post(
        "/api/sessions",
        json={"unit_id": unit_id, "content_type": "chinese"},
        headers=_auth(student_token),
    ).json()
    check(
        "开语文场次",
        session_body["code"] == 0 and session_body["data"]["content_type"] == "chinese",
        f"code={session_body['code']}",
    )
    session_id = session_body["data"]["session_id"]
    items = session_body["data"]["items"]
    check(
        "场次题目 = 课文清单（3 篇）",
        len(items) == 3 and items[0]["text_id"] == text_id and items[0]["recite_scope"] == [0],
        f"n={len(items)} scope={items[0].get('recite_scope') if items else '-'}",
    )
    check(
        "组卷响应不含正文",
        all("content_raw" not in item and "content_plain" not in item for item in items),
        "",
    )

    detail = client.get(f"/api/texts/{text_id}", headers=_auth(student_token)).json()
    check(
        "§8.7 课文详情含背诵范围、不下发拼音",
        detail["code"] == 0
        and detail["data"]["scope_snapshot"]["end"] > 0
        and "content_pinyin" not in detail["data"],
        f"snapshot={detail['data'].get('scope_snapshot') if detail['code'] == 0 else '-'}",
    )

    # ---- E. 创建背诵 ----
    created = client.post(
        "/api/recitations",
        json={"session_id": session_id, "text_id": text_id, "scope": []},
        headers=_auth(student_token),
    ).json()
    check(
        "创建背诵（F1）",
        created["code"] == 0 and created["data"]["total_chars"] > 0,
        f"code={created['code']} total={created['data'].get('total_chars') if created['code'] == 0 else '-'}",
    )
    data = created["data"]
    rid = data["recitation_id"]
    check(
        "F1 不下发拼音与正文",
        not {"content_pinyin", "content_plain", "content_raw"} & set(data),
        f"keys={sorted(data)[:6]}",
    )
    check(
        "F1 返回每段字数（scope 为空 = 课文背诵范围）",
        data["char_count_per_paragraph"] == [7],
        f"{data.get('char_count_per_paragraph')}",
    )

    # 范围裁剪：另起一次背诵，只背第 1 段
    scoped = client.post(
        "/api/recitations",
        json={"session_id": session_id, "text_id": text_id, "scope": [0]},
        headers=_auth(student_token),
    ).json()
    check(
        "按范围裁剪 total_chars",
        scoped["code"] == 0 and scoped["data"]["total_chars"] == 7,
        f"total={scoped['data'].get('total_chars') if scoped['code'] == 0 else '-'}",
    )

    # 背诵范围不含首段时，字符区间不能错位（BE-05 修掉的真实缺陷）
    lotus = _sql(db_file, "SELECT id FROM texts WHERE title = '荷塘月色'")[0]["id"]
    lotus_recite = client.post(
        "/api/recitations",
        json={"session_id": session_id, "text_id": lotus, "scope": []},
        headers=_auth(student_token),
    ).json()
    check(
        "背诵范围不含首段时切片不错位",
        lotus_recite["code"] == 0
        and lotus_recite["data"]["total_chars"] == 15
        and lotus_recite["data"]["char_count_per_paragraph"] == [9, 6],
        f"total={lotus_recite['data'].get('total_chars') if lotus_recite['code'] == 0 else '-'} "
        f"per={lotus_recite['data'].get('char_count_per_paragraph') if lotus_recite['code'] == 0 else '-'}",
    )

    out_of_scope = client.post(
        "/api/recitations",
        json={"session_id": session_id, "text_id": lotus, "scope": [0]},
        headers=_auth(student_token),
    ).json()
    check(
        "请求未标记背诵的段落被拒（10001）",
        out_of_scope["code"] == 10001,
        f"code={out_of_scope['code']}",
    )

    # ---- F. 流式喂识别结果 ----
    feed = client.post(
        f"/api/recitations/{rid}/feed",
        json={"partial_text": "我与父亲"},
        headers=_auth(student_token),
    ).json()
    check(
        "feed 推进指针（U1）",
        feed["code"] == 0 and feed["data"]["pos"] == 4 and feed["data"]["newly_matched"] == [0, 1, 2, 3],
        f"pos={feed['data'].get('pos') if feed['code'] == 0 else '-'}",
    )

    # ---- G. 提示机制（此刻 pos=4，还剩 3 个字，提示有内容可给）----
    hint_feed = client.post(
        f"/api/recitations/{rid}/feed",
        json={"partial_text": "我与父亲", "silence_ms": 8000},
        headers=_auth(student_token),
    ).json()
    check(
        "feed 附带提示建议（不消耗档位）",
        hint_feed["code"] == 0 and hint_feed["data"]["hint_suggestion"] == 1,
        f"suggestion={hint_feed['data'].get('hint_suggestion') if hint_feed['code'] == 0 else '-'}",
    )

    auto = client.post(
        f"/api/recitations/{rid}/hint",
        json={"level": None, "trigger_by": "auto", "silence_ms": 8000},
        headers=_auth(student_token),
    ).json()
    check(
        "静音自动给一档提示（U8）",
        auto["code"] == 0
        and auto["data"]["level"] == 1
        and auto["data"]["content"] == "_" * 3,
        f"level={auto['data'].get('level') if auto['code'] == 0 else '-'} content={auto['data'].get('content')!r}",
    )

    again = client.post(
        f"/api/recitations/{rid}/hint",
        json={"level": None, "trigger_by": "auto", "silence_ms": 8000},
        headers=_auth(student_token),
    ).json()
    check(
        "同一位置不重复触发（U9）",
        again["code"] == 0 and again["data"]["level"] is None,
        f"level={again['data'].get('level') if again['code'] == 0 else '-'}",
    )

    higher = client.post(
        f"/api/recitations/{rid}/hint",
        json={"level": None, "trigger_by": "auto", "silence_ms": 16000},
        headers=_auth(student_token),
    ).json()
    check(
        "静音更久升到二档（U8）",
        higher["code"] == 0 and higher["data"]["level"] == 2 and higher["data"]["content"] == "不",
        f"level={higher['data'].get('level') if higher['code'] == 0 else '-'}",
    )

    manual = client.post(
        f"/api/recitations/{rid}/hint",
        json={"level": 3, "trigger_by": "manual"},
        headers=_auth(student_token),
    ).json()
    check(
        "手动三档提示返回内容（U10）",
        manual["code"] == 0 and manual["data"]["content"] == "不相见",
        f"content={manual['data'].get('content')!r}",
    )
    check(
        "提示分档计数",
        manual["code"] == 0
        and manual["data"]["hint_count"] == {"1": 1, "2": 1, "3": 1},
        f"{manual['data'].get('hint_count')}",
    )

    # ---- H. 继续背完 + 噪声 + 重来 ----
    feed = client.post(
        f"/api/recitations/{rid}/feed",
        json={"partial_text": "我与父亲不相见"},
        headers=_auth(student_token),
    ).json()
    check(
        "累计文本只消费增量（U2）",
        feed["code"] == 0 and feed["data"]["pos"] == 7 and feed["data"]["newly_matched"] == [4, 5, 6],
        f"pos={feed['data'].get('pos') if feed['code'] == 0 else '-'}",
    )

    noise = client.post(
        f"/api/recitations/{rid}/feed",
        # `partial_text` 是**累计**文本，所以噪声也要接在已识别内容之后
        # （直接送一句等长的无关话会被当成「没有新增」而忽略）
        json={"partial_text": "我与父亲不相见今天天气真不错"},
        headers=_auth(student_token),
    ).json()
    check(
        "噪声片段不推进（U6）",
        noise["code"] == 0 and noise["data"]["is_noise"] is True and noise["data"]["pos"] == 7,
        f"noise={noise['data'].get('is_noise') if noise['code'] == 0 else '-'}",
    )

    restarted = client.post(f"/api/recitations/{rid}/restart", headers=_auth(student_token)).json()
    check(
        "重新开始本段（§5.6）",
        restarted["code"] == 0
        and restarted["data"]["pos"] == 0
        and restarted["data"]["restart_count"] == 1,
        f"pos={restarted['data'].get('pos') if restarted['code'] == 0 else '-'}",
    )

    # 同音字（另起一次背诵）
    homo = client.post(
        "/api/recitations",
        json={"session_id": session_id, "text_id": rows[1]["id"], "scope": []},
        headers=_auth(student_token),
    ).json()
    homo_id = homo["data"]["recitation_id"]
    homo_feed = client.post(
        f"/api/recitations/{homo_id}/feed",
        json={"partial_text": "盼望着盼望着"},
        headers=_auth(student_token),
    ).json()
    check(
        "同音字全部点亮",
        homo_feed["code"] == 0 and homo_feed["data"]["matched_chars"] == 6,
        f"matched={homo_feed['data'].get('matched_chars') if homo_feed['code'] == 0 else '-'}",
    )

    # 漏字（第 1 篇第 1 段「我与父亲不相见」）
    miss = client.post(
        "/api/recitations",
        json={"session_id": session_id, "text_id": text_id, "scope": [0]},
        headers=_auth(student_token),
    ).json()
    miss_id = miss["data"]["recitation_id"]
    miss_feed = client.post(
        f"/api/recitations/{miss_id}/feed",
        json={"partial_text": "我与父亲相见"},
        headers=_auth(student_token),
    ).json()
    session_after = client.post(
        f"/api/recitations/{miss_id}/settle",
        json={"transcript": "我与父亲相见", "finish_reason": "manual"},
        headers=_auth(student_token),
    ).json()
    check(
        "漏字记入 missed 且指针继续推进（U4）",
        miss_feed["code"] == 0
        and miss_feed["data"]["pos"] == 7
        and session_after["data"]["missed_count"] == 1,
        f"pos={miss_feed['data'].get('pos')} missed={session_after['data'].get('missed_count')}",
    )

    # ---- I. 事件补报 ----
    reported = client.post(
        f"/api/recitations/{rid}/events",
        json={"events": [{"pos": 0, "event_type": "noise"}]},
        headers=_auth(student_token),
    ).json()
    check(
        "App 断线补报事件（F2）",
        reported["code"] == 0 and reported["data"]["accepted"] == 1,
        f"accepted={reported['data'].get('accepted') if reported['code'] == 0 else '-'}",
    )

    # ---- J. 结算（谎报数据，验服务端重算）----
    client.post(
        f"/api/recitations/{rid}/feed",
        json={"partial_text": "我与父亲"},
        headers=_auth(student_token),
    )
    settled = client.post(
        f"/api/recitations/{rid}/settle",
        json={
            "transcript": "我与父亲",
            "matched_chars": 999,          # 谎报：服务端必须忽略
            "missed_chars": [],            # 谎报
            "hint_count": {"1": 0, "2": 0, "3": 0},  # 谎报
            "restart_count": 1,
            "duration_ms": 42000,
            "first_voice_ms": 1500,
            "finish_reason": "manual",
        },
        headers=_auth(student_token),
    ).json()
    check(
        "F5 服务端重算（谎报 matched_chars 无效）",
        settled["code"] == 0 and settled["data"]["matched_chars"] == 4,
        f"matched={settled['data'].get('matched_chars') if settled['code'] == 0 else '-'}",
    )
    check(
        "结算返回覆盖比例/分数/评级",
        settled["code"] == 0
        and 0 < settled["data"]["covered_ratio"] < 1
        and settled["data"]["grade"] in ("优秀", "良好", "合格", "待加强"),
        f"ratio={settled['data'].get('covered_ratio')} score={settled['data'].get('score')} grade={settled['data'].get('grade')}",
    )
    check(
        "结算回显漏字明细（含具体字）",
        settled["code"] == 0
        and [d["char"] for d in settled["data"]["missed_detail"]] == ["不", "相", "见"],
        f"{settled['data'].get('missed_detail')}",
    )
    check(
        "提示次数以服务端为准（谎报无效）",
        settled["code"] == 0 and settled["data"]["hint_count"] == {"1": 1, "2": 1, "3": 1},
        f"{settled['data'].get('hint_count')}",
    )

    replay = client.post(
        f"/api/recitations/{rid}/settle",
        json={"transcript": "我与父亲", "finish_reason": "manual"},
        headers=_auth(student_token),
    ).json()
    check(
        "重复结算幂等",
        replay["code"] == 0
        and replay["data"]["replay"] is True
        and replay["data"]["matched_chars"] == settled["data"]["matched_chars"],
        f"replay={replay['data'].get('replay') if replay['code'] == 0 else '-'}",
    )

    # ---- K. 落库校验 ----
    row = _sql(db_file, "SELECT * FROM recitations WHERE id = ?", (rid,))[0]
    check(
        "recitations 落库（transcript/分数/评级/结束方式）",
        row["finish_reason"] == "manual"
        and row["matched_chars"] == 4
        and row["score"] == settled["data"]["score"]
        and row["duration_ms"] == 42000
        and row["first_voice_ms"] == 1500,
        f"matched={row['matched_chars']} score={row['score']} reason={row['finish_reason']}",
    )
    check(
        "recitations 记录背诵范围快照",
        json.loads(row["scope_snapshot"])["end"] - json.loads(row["scope_snapshot"])["start"] == 7,
        f"{row['scope_snapshot']}",
    )

    events = _sql(
        db_file,
        "SELECT seq, pos, event_type, hint_level, trigger_by FROM recitation_events "
        "WHERE recitation_id = ? ORDER BY seq",
        (rid,),
    )
    kinds = {e["event_type"] for e in events}
    check(
        "recitation_events 事件齐全（U14）",
        {"matched", "hint"} <= kinds,
        f"kinds={sorted(kinds)} n={len(events)}",
    )
    check(
        "事件 seq 连续有序（U14）",
        [e["seq"] for e in events] == list(range(1, len(events) + 1)),
        f"n={len(events)}",
    )
    hint_events = [e for e in events if e["event_type"] == "hint"]
    check(
        "提示事件记录档位与触发方式（§5.7）",
        len(hint_events) >= 3
        and {e["trigger_by"] for e in hint_events} <= {"manual", "auto"}
        and {e["hint_level"] for e in hint_events} <= {1, 2, 3},
        f"n={len(hint_events)} levels={sorted({e['hint_level'] for e in hint_events})}",
    )

    session_row = _sql(db_file, "SELECT status, score, total_items, correct_items FROM sessions WHERE id = ?", (session_id,))[0]
    check(
        "背诵统计同步到场次（字为口径）",
        # 此刻已结算两次（7 字/点亮 4 + 7 字/点亮 6），未结算的不计入
        session_row["status"] == "ongoing"
        and session_row["total_items"] == 14
        and session_row["correct_items"] == 10,
        f"status={session_row['status']} total={session_row['total_items']} correct={session_row['correct_items']}",
    )

    # 进程重启模拟：引擎不在内存时用 transcript 重建（趁场次还开着）
    again_recite = client.post(
        "/api/recitations",
        json={"session_id": session_id, "text_id": rows[1]["id"], "scope": []},
        headers=_auth(student_token),
    ).json()
    check(
        "背完一篇后场次仍可继续",
        again_recite["code"] == 0,
        f"code={again_recite['code']}",
    )
    fresh_id = again_recite["data"]["recitation_id"]

    recovered = client.post(
        f"/api/recitations/{fresh_id}/settle",
        json={"transcript": "盼望着盼望着", "finish_reason": "completed"},
        headers=_auth(student_token),
    ).json()
    check(
        "无实时状态也能结算（transcript 重建）",
        recovered["code"] == 0 and recovered["data"]["covered_ratio"] == 1.0,
        f"ratio={recovered['data'].get('covered_ratio') if recovered['code'] == 0 else '-'}",
    )

    finished = client.post(
        f"/api/sessions/{session_id}/finish",
        json={"reason": "completed"},
        headers=_auth(student_token),
    ).json()
    check(
        "显式收口语文场次（按篇汇总）",
        finished["code"] == 0
        and finished["data"]["content_type"] == "chinese"
        and finished["data"]["status"] == "finished"
        and finished["data"]["recitations"] >= 1,
        f"code={finished['code']} recitations={finished['data'].get('recitations') if finished['code'] == 0 else '-'}",
    )

    # ---- L. 异常与权限 ----
    other = client.post(
        "/api/auth/register",
        json={"username": "20260906", "password": "Student-Pass-2", "real_name": "另一个学生"},
    ).json()
    other_token = other["data"]["access_token"]

    denied = client.post(
        f"/api/recitations/{rid}/feed",
        json={"partial_text": "我与父亲"},
        headers=_auth(other_token),
    ).json()
    check("越权访问他人背诵被拒（10004）", denied["code"] == 10004, f"code={denied['code']}")

    anonymous = client.post(
        f"/api/recitations/{rid}/feed", json={"partial_text": "我"}
    ).json()
    check("未登录被拒（10002）", anonymous["code"] == 10002, f"code={anonymous['code']}")

    bad_scope = client.post(
        "/api/recitations",
        json={"session_id": session_id, "text_id": text_id, "scope": [9]},
        headers=_auth(student_token),
    ).json()
    check("非法背诵范围被拒（10001）", bad_scope["code"] == 10001, f"code={bad_scope['code']}")

    bad_reason = client.post(
        f"/api/recitations/{fresh_id}/settle",
        json={"transcript": "盼望着盼望着", "finish_reason": "boom"},
        headers=_auth(student_token),
    ).json()
    check("非法 finish_reason 被拒（10001）", bad_reason["code"] == 10001, f"code={bad_reason['code']}")

    expired = client.post(
        "/api/recitations/999999/settle",
        json={"transcript": "x", "finish_reason": "manual"},
        headers=_auth(student_token),
    ).json()
    check("不存在的背诵（10004）", expired["code"] == 10004, f"code={expired['code']}")


def main() -> int:
    if _port_busy(PORT):
        print(f"[!!] port {PORT} 已被占用，换个端口：SMOKE_PORT=xxxx")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="bjsys_smoke_be05_"))
    db_file = workdir / "data" / "smoke.db"
    log_file = workdir / "uvicorn.log"

    env = dict(os.environ)
    env.update(
        {
            "DB_PATH": str(db_file),
            "BACKUP_DIR": str(workdir / "backups"),
            "JWT_SECRET": "smoke-be05-secret-0123456789abcdefghij",
            "JWT_TTL_HOURS": "12",
            "ADMIN_INIT_USERNAME": ADMIN_USER,
            "ADMIN_INIT_PASSWORD": ADMIN_PASSWORD,
            "LOG_LEVEL": "WARNING",
            "APP_PORT": str(PORT),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )

    print(f"== 起真 uvicorn :{PORT}（临时库 {db_file}）==", flush=True)
    with log_file.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning",
            ],
            cwd=str(BACKEND),
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
        )

    try:
        if not _wait_ready():
            print("[!!] 服务未能就绪，日志尾部：")
            print(log_file.read_text(encoding="utf-8", errors="replace")[-3000:])
            return 1

        with httpx.Client(base_url=BASE, timeout=30.0) as c:
            _run_all(c, db_file)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(workdir, ignore_errors=True)
        print("== 已收尸（子进程 + 临时目录）==", flush=True)

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    print()
    print(f"烟测结果：{passed}/{total} 通过", flush=True)
    for ok, name, detail in _results:
        if not ok:
            print(f"  FAILED  {name}  |  {detail[:160]}", flush=True)
    print(f"SUMMARY pass={passed} total={total} failed={total - passed}", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
