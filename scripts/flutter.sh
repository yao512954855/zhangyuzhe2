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

# ②'' ProgramFiles 系列兜底 —— **flutter test 专用坑**
#
#   踩过的坑：WorkBuddy 的 bash 沙箱可能把 ProgramFiles 系列环境变量剥掉。
#   `flutter test` 会跑 native-assets 钩子，其中 Windows 目标要探测
#   Visual Studio（vswhere.exe 固定在 %ProgramFiles(x86)%\Microsoft Visual
#   Studio\Installer\ 下）——变量一丢，flutter_tools 直接 throwToolExit
#   「%PROGRAMFILES(X86)% environment variable not found」退出码 1，
#   **一个字报错都不给**（2026-09-28 实测，-v 才看到堆栈）。
#   analyze/build 不走这条钩子，所以只有 test 会崩。
#
#   注意：`PROGRAMFILES(X86)` 带括号，**不是合法 bash 标识符**，export 会报
#   "not a valid identifier"，只能在最后 exec 时用 `env` 注入（env 直接
#   execve 传参，不受标识符规则限制）。见脚本末尾。

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

# ③' 构建缓存一律不落 C 盘（2026-10-01 姚工要求：构建时不要用 C 盘）
#
#   ⚠️ 默认值必须指到**热缓存** D:/dev-cache/{gradle,pub}（2026-10-01 实锤）：
#   本机用户级环境变量本来就是 GRADLE_USER_HOME=D:\dev-cache\gradle（1.4G 热依赖
#   + init.d/aliyun-mirror.gradle 镜像）。曾改指 D:/2install/.gradle-home（冷目录），
#   结果全量重新下载依赖 —— 第一次忘拷镜像配置直连 mavenCentral 卡死 26 分钟，
#   第二次拷了仍有一个构件下载失败。dev-cache 里 init.d + 热缓存都是现成的。
#   两个都允许外部覆盖（${VAR:-...}），CI / 个人机可再指到别处。
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-D:/dev-cache/gradle}"
export PUB_CACHE="${PUB_CACHE:-D:/dev-cache/pub}"

# 镜像 init 兜底：换了全新 GRADLE_USER_HOME（没有 init.d 也没有 init.gradle）时，
# 把 C 盘的镜像配置同步过去，避免回落 mavenCentral 直连被限速卡死。
if [ ! -f "$GRADLE_USER_HOME/init.gradle" ] && [ ! -d "$GRADLE_USER_HOME/init.d" ] \
   && [ -f "$HOME/.gradle/init.gradle" ]; then
  mkdir -p "$GRADLE_USER_HOME"
  cp "$HOME/.gradle/init.gradle" "$GRADLE_USER_HOME/init.gradle"
fi

# ④ Flutter SDK 位置（改路径只改这一处）
FLUTTER_HOME="${FLUTTER_HOME:-D:/2install/flutter/flutter_windows_3.47.5-stable/flutter}"

if [ ! -f "$FLUTTER_HOME/bin/flutter.bat" ]; then
  echo "❌ 找不到 Flutter SDK: $FLUTTER_HOME" >&2
  echo "   请检查 FLUTTER_HOME 环境变量，或修改本脚本第 34 行。" >&2
  exit 1
fi

# ProgramFiles 系列若被沙箱剥掉则补回（带括号的那个只能靠 env 注入，
# 值写死也无妨——vswhere 只用它拼 System32 之外的固定安装探测路径）。
_PF86="C:\\Program Files (x86)"
_PF="C:\\Program Files"
if [ -n "${PROGRAMFILES:-}" ]; then _PF="$PROGRAMFILES"; fi
if [ -n "${PROGRAMFILES_X86_OVERRIDE:-}" ]; then
  _PF86="$PROGRAMFILES_X86_OVERRIDE"
elif [ -n "${PROGRAMFILES_X86:-}" ]; then
  _PF86="$PROGRAMFILES_X86"
fi

exec env "PROGRAMFILES=$_PF" "PROGRAMFILES(X86)=$_PF86" "$FLUTTER_HOME/bin/flutter.bat" "$@"
