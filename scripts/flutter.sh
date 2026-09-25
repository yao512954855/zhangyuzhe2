#!/usr/bin/env bash
# ============================================================
# flutter.sh —— 本项目在这台机器上运行 Flutter 的统一入口
#
# 【为什么需要这个包装脚本】
#   WorkBuddy 的 bash 环境（MSYS / Git-Bash）**没有继承 Windows 的 PATHEXT**。
#   而 flutter_tools 依赖 `where <exe>` 来定位可执行文件（package:process）。
#   缺 PATHEXT 时 `where git` 找不到 git.exe（.exe 后缀不会被自动补全），
#   package:process 返回 null，Flutter 直接崩在版本检查：
#       Null check operator used on a null value
#       #0  getExecutablePath (package:process/src/interface/common.dart:60)
#       #8  Git.logSync
#       #9  new FlutterVersion
#
#   —— 注意：**这个问题只影响从 bash 调用**。
#      在 Windows 的 CMD / PowerShell 里跑 flutter 是正常的（那边有 PATHEXT）。
#
# 【用法】
#   scripts/flutter.sh doctor
#   scripts/flutter.sh --version
#   scripts/flutter.sh create --platforms=android,web student-app
#   scripts/flutter.sh run -d chrome
#   scripts/flutter.sh build apk --release
# ============================================================
set -e

# ① 补回 MSYS 环境丢失的 PATHEXT（关键修复，删掉这行 Flutter 必崩）
export PATHEXT='.COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC'

# ② 国内镜像（github / pub.dev 直连不通，doctor 已实测 502）
export PUB_HOSTED_URL="${PUB_HOSTED_URL:-https://pub.flutter-io.cn}"
export FLUTTER_STORAGE_BASE_URL="${FLUTTER_STORAGE_BASE_URL:-https://storage.flutter-io.cn}"

# ②' 局域网地址绕过代理，否则连本机后端（8810）会走代理
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12}"
export no_proxy="$NO_PROXY"

# ③ Android SDK / JDK —— **必须显式导出，不能只靠 `flutter config`**
#
#   踩过的坑：`flutter config --android-sdk/--jdk-dir` 写出的 .flutter_settings
#   落在**执行时的当前目录**，只在那个目录下生效。一旦 cd 进 student-app/ 之类的
#   子目录，配置就全部读不到（`flutter config --list` 全 "Not set"），
#   doctor 直接报 "Unable to locate Android SDK"。
#   （%APPDATA%\Roaming\.flutter_settings 是 2025 年旧版 3.35.2 的遗留物，3.47.5 不读。）
#
#   环境变量优先级稳定且与目录无关，所以这里显式导出：
export ANDROID_HOME="${ANDROID_HOME_OVERRIDE:-D:/2install/androidstudiotool/sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
# 用 Android Studio 自带的 JBR（OpenJDK 25）——**只影响本脚本进程**，
# 不动机器级 JAVA_HOME（那里是 JDK 1.8，是 D 盘老工具链在用的，别去改）。
export JAVA_HOME="${JAVA_HOME_OVERRIDE:-D:/2install/androidstudiotool/dir/jbr}"

# ④ Flutter SDK 位置（改路径只改这一处）
FLUTTER_HOME="${FLUTTER_HOME:-D:/2install/flutter/flutter_windows_3.47.5-stable/flutter}"

if [ ! -f "$FLUTTER_HOME/bin/flutter.bat" ]; then
  echo "❌ 找不到 Flutter SDK: $FLUTTER_HOME" >&2
  echo "   请检查 FLUTTER_HOME 环境变量，或修改本脚本第 34 行。" >&2
  exit 1
fi

exec "$FLUTTER_HOME/bin/flutter.bat" "$@"
