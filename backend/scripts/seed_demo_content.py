"""为「模拟器 / 真机验收」造演示数据：admin 登录 → 导入英语词条 + 语文课文。

为什么要这个脚本
----------------
`data/bjsys.db` 是干净库（只有 admin + settings 种子），**没有内容**。
而 C9（真机/模拟器整体跑一遍）要求「完成一次英语练习 + 一次语文背诵」，
没有内容就走不到练习页。本脚本用**正式导入链路**（preview → commit）把
`qa/fixtures/` 下的样例灌进库，**不绕过业务校验、不直接改表**。

用法
----
    cd backend
    env -u PYTHONPATH .venv/Scripts/python.exe scripts/seed_demo_content.py

    # 指定后端地址（默认 http://127.0.0.1:8810）
    env -u PYTHONPATH .venv/Scripts/python.exe scripts/seed_demo_content.py --base http://127.0.0.1:8810

注意
----
- **只登录一次**：契约 §5.4 登录限流是 5 次/分钟且「成功也计数」，
  脚本复用同一枚 token，避免撞 10005。
- 幂等：词条按 `(unit_id, en)` 判重，`on_duplicate=overwrite`，重复跑不会炸。
- 用 `httpx`（backend venv 已带），**不引入新依赖**。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import httpx

#: 仓库根（本文件在 backend/scripts/ 下）
ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "qa" / "fixtures"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _dump(tag: str, resp: httpx.Response, limit: int = 900) -> dict:
    """打印并返回响应体（非 JSON 时退化为文本，便于看 500 栈）。"""
    try:
        body = resp.json()
    except ValueError:
        print(f"[{tag}] HTTP {resp.status_code} (非 JSON 响应)")
        print(resp.text[:limit])
        return {}
    print(f"[{tag}] HTTP {resp.status_code}")
    print(json.dumps(body, ensure_ascii=False, indent=2)[:limit])
    return body


def _admin_headers(base: str, password: str) -> dict[str, str]:
    resp = httpx.post(
        f"{base}/api/auth/login",
        json={"username": "admin", "password": password, "role": "admin"},
        timeout=15,
    )
    body = _dump("login(admin)", resp)
    if body.get("code") != 0:
        sys.exit("✗ admin 登录失败，终止（先确认 .env 的 ADMIN_INIT_PASSWORD 与后端已启动）")
    token = body["data"]["access_token"]
    print("✓ admin 登录成功，token 已取得")
    return {"Authorization": f"Bearer {token}"}


def _import(base: str, headers: dict[str, str], kind: str, filename: str) -> None:
    """走 preview → commit 两步导入（kind ∈ {words, texts}）。"""
    path = FIXTURES / filename
    if not path.exists():
        sys.exit(f"✗ 找不到样例文件：{path}")

    with path.open("rb") as fh:
        resp = httpx.post(
            f"{base}/api/import/{kind}/preview",
            files={"file": (filename, fh, XLSX_MIME)},
            headers=headers,
            timeout=60,
        )
    preview = _dump(f"{kind}.preview", resp)
    if preview.get("code") != 0:
        sys.exit(f"✗ {kind} preview 失败，终止")

    tok = preview["data"]["preview_token"]
    payload: dict = {"preview_token": tok}
    if kind == "words":
        payload.update(create_missing_units=True, on_duplicate="overwrite")
    else:
        # texts：留空表示全部按 preview 的默认背诵范围入库
        payload.update(texts=[])

    resp = httpx.post(
        f"{base}/api/import/{kind}/commit", json=payload, headers=headers, timeout=60
    )
    committed = _dump(f"{kind}.commit", resp)
    if committed.get("code") != 0:
        sys.exit(f"✗ {kind} commit 失败，终止")
    print(f"✓ {kind} 导入完成")


def main() -> None:
    parser = argparse.ArgumentParser(description="为模拟器/真机验收造演示数据")
    parser.add_argument("--base", default="http://127.0.0.1:8810", help="后端地址")
    parser.add_argument(
        "--password",
        default="bjsys-dev-2026",
        help="admin 密码（默认取 .env 的开发值，可覆盖）",
    )
    args = parser.parse_args()

    headers = _admin_headers(args.base, args.password)
    _import(args.base, headers, "words", "words_import.xlsx")
    _import(args.base, headers, "texts", "texts_import.xlsx")
    print("\n✓ 演示数据就绪：英语 Unit 1 Hello! + 语文《背影（节选）》")


if __name__ == "__main__":
    main()
