#!/usr/bin/env python3
"""背记系统 · Linux 发布包打包脚本（在 Windows 开发机上跑）。

用法：
    python scripts/pack_release.py            # 用现有管理端构建产物打包
    python scripts/pack_release.py --build    # 先重新构建管理端（npm run build）再打包

产出：release/bjsys-linux-<日期时间>.tar.gz
包内结构：
    bjsys/
      install.sh / start.sh / stop.sh / 运行说明.md
      backend/  (app源码 + requirements.txt + asr.yaml + .env + static/admin + 空 data/)
"""
from __future__ import annotations

import argparse
import io
import secrets
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
ADMIN = ROOT / "admin-web"
DEPLOY = ROOT / "deploy" / "linux"
OUT_DIR = ROOT / "release"

IGNORE_DIRS = {"__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache"}


def copy_tree(src: Path, dst: Path, excludes: list[str] | None = None) -> None:
    excludes = excludes or []
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if any(p in IGNORE_DIRS for p in rel.parts):
            continue
        if any(rel.match(pat) for pat in excludes):
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def build_admin() -> None:
    print(">> 构建管理端 (npm run build) ...")
    subprocess.run(["npm", "run", "build"], cwd=ADMIN, check=True, shell=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="强制重新构建管理端")
    args = ap.parse_args()

    # ---- 0. 管理端静态产物 ----
    static_admin = BACKEND / "static" / "admin"
    if args.build or not static_admin.joinpath("index.html").exists():
        dist = ADMIN / "dist"
        if args.build or not dist.joinpath("index.html").exists():
            build_admin()
        if static_admin.exists():
            shutil.copytree(dist, static_admin, dirs_exist_ok=True)
        else:
            shutil.copytree(dist, static_admin)
    print(f">> 管理端静态产物：{static_admin}")

    # ---- 1. 包目录（时间戳命名，绝不覆盖旧包）----
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    pkg_src = OUT_DIR / f"bjsys-linux-{stamp}"
    if pkg_src.exists():
        print(f"[错误] {pkg_src} 已存在（一分钟内重复打包？），换目录再试")
        sys.exit(1)
    backend_dst = pkg_src / "backend"
    backend_dst.mkdir(parents=True)
    print(f">> 组装 {pkg_src} ...")

    # ---- 2. 后端代码 ----
    copy_tree(BACKEND / "app", backend_dst / "app")
    shutil.copy2(BACKEND / "requirements.txt", backend_dst / "requirements.txt")
    (backend_dst / "data").mkdir(exist_ok=True)  # 空目录，首启自动建库
    (backend_dst / "logs").mkdir(exist_ok=True)

    # 离线导入脚本（word2excel 等），排除开发调试痕迹
    if (BACKEND / "scripts").exists():
        copy_tree(BACKEND / "scripts", backend_dst / "scripts",
                  excludes=["_cn_trace.py", "*.bak", "*.log"])

    # ---- 3. asr.yaml（语音配置，含 key，git 里没有）----
    if (BACKEND / "asr.yaml").exists():
        shutil.copy2(BACKEND / "asr.yaml", backend_dst / "asr.yaml")
        print(">> 已包含 backend/asr.yaml（语音配置）")
    else:
        print("!! 警告：backend/asr.yaml 不存在，包内语音不可用（其他功能不受影响）")

    # ---- 4. .env：沿用现配置，但换全新 JWT_SECRET ----
    src_env = BACKEND / ".env"
    if not src_env.exists():
        print("[错误] backend/.env 不存在，无法打包（配置从哪来？）")
        sys.exit(1)
    env = read_env(src_env)
    env["APP_HOST"] = "0.0.0.0"
    env["JWT_SECRET"] = secrets.token_hex(32)
    lines = [f"{k}={v}" for k, v in env.items()]
    (backend_dst / ".env").write_text(
        "# 发布包自动生成（JWT_SECRET 为全新随机值）\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(f">> .env 已生成（管理员账号: {env.get('ADMIN_INIT_USERNAME', 'admin')}，"
          f"密码沿用开发机 .env，交付时口头告知并提醒改密）")

    # ---- 5. 学生端 APK（release 优先，debug 兜底）----
    apk_dir = ROOT / "student-app" / "build" / "app" / "outputs" / "flutter-apk"
    apk = apk_dir / "app-release.apk"
    apk_note = "release"
    if not apk.exists():
        apk = apk_dir / "app-debug.apk"
        apk_note = "debug（体积大，建议 flutter build apk --release 后重打）"
    if apk.exists():
        shutil.copy2(apk, pkg_src / "学生端APP.apk")
        print(f">> 已包含学生端 APK（{apk_note}，{apk.stat().st_size / 1048576:.0f} MB）")
    else:
        print("!! 警告：没找到 APK，学生端需另行分发")

    # ---- 6. 管理端静态 + Linux 脚本（统一 LF，脚本要可执行）----
    shutil.copytree(static_admin, backend_dst / "static" / "admin")
    scripts: list[tuple[Path, int]] = [
        (DEPLOY / "install.sh", 0o755),
        (DEPLOY / "start.sh", 0o755),
        (DEPLOY / "stop.sh", 0o755),
        (DEPLOY / "运行说明.md", 0o644),
    ]
    for src, mode in scripts:
        dst = pkg_src / src.name
        dst.write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))  # LF 规范化
        dst.chmod(mode)

    # ---- 7. tar.gz（保持可执行位）----
    out = OUT_DIR / f"{pkg_src.name}.tar.gz"
    print(f">> 压缩 {out.name} ...")
    with tarfile.open(out, "w:gz") as tar:
        for item in sorted(pkg_src.rglob("*")):
            ti = tar.gettarinfo(item, arcname=Path("bjsys") / item.relative_to(pkg_src))
            if item.is_dir():
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                ti.size = 0
                tar.addfile(ti)
            else:
                ti.mode = 0o755 if item.suffix == ".sh" else 0o644
                with open(item, "rb") as f:
                    tar.addfile(ti, f)

    size_mb = out.stat().st_size / 1024 / 1024
    print()
    print("=" * 50)
    print(f" 发布包完成: {out}")
    print(f" 大小: {size_mb:.1f} MB")
    print(f" 交付方式: 把这一个文件拷给对方，解压后按包内《运行说明.md》三步走")
    print("=" * 50)
    # 清掉中间目录，只留 tar.gz
    shutil.rmtree(pkg_src)


if __name__ == "__main__":
    main()
