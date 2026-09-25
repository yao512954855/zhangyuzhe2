/// 通用工具：颜色对比度校验 + 展示格式化。
///
/// **为什么对比度计算要放在 App 里而不是只写在设计文档里**：
/// UI 规范 §2.1 把「正文对比度 ≥ 4.5:1（WCAG AA）」列为硬约束（**不设上限**，
/// 原文「8:1 ~ 12:1」已废止），但色值一旦被人手改一个字符
/// （比如夜间背景从 `#1B1F23` 改成 `#14171A`），
/// 文档不会报错、眼睛也未必立刻察觉。把它变成可执行的断言，
/// 才是真的约束住了（见 `test/test_app01_theme.dart`）。
library;

import 'dart:math' as math;

/// 把 `#RRGGBB` / `RRGGBB` 解析为 `0xRRGGBB`。
///
/// 非法输入抛 [FormatException]——**故意不静默兜底**：
/// 主题里写错一个色值属于开发期错误，越早炸越好，不该让 App 带着错颜色上线。
int parseHex(String hex) {
  var s = hex.trim();
  if (s.startsWith('#')) {
    s = s.substring(1);
  }
  if (s.length == 3) {
    // #ABC → #AABBCC
    s = s.split('').map((c) => '$c$c').join();
  }
  if (s.length != 6) {
    throw FormatException('色值长度不对（应为 #RRGGBB）：$hex');
  }
  final value = int.tryParse(s, radix: 16);
  if (value == null) {
    throw FormatException('色值含非法字符：$hex');
  }
  return value;
}

/// 拆出 `(r, g, b)`，各通道 0–255。
(int, int, int) rgbOf(int rgb) => (
      (rgb >> 16) & 0xFF,
      (rgb >> 8) & 0xFF,
      rgb & 0xFF,
    );

/// sRGB 通道线性化（WCAG 2.x 定义）。
double _linearize(int channel) {
  final s = channel / 255.0;
  if (s <= 0.03928) {
    return s / 12.92;
  }
  return math.pow((s + 0.055) / 1.055, 2.4).toDouble();
}

/// WCAG 相对亮度（0 = 黑，1 = 白）。
double relativeLuminance(int rgb) {
  final (r, g, b) = rgbOf(rgb);
  return 0.2126 * _linearize(r) +
      0.7152 * _linearize(g) +
      0.0722 * _linearize(b);
}

/// 两色对比度比值，范围 `1.0 ~ 21.0`。
///
/// 比值与顺序无关（内部自动取亮的做分子）。
double contrastRatio(int a, int b) {
  final la = relativeLuminance(a);
  final lb = relativeLuminance(b);
  final hi = math.max(la, lb);
  final lo = math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

/// 秒数 → `m:ss`（计时、倒计时用；等宽数字显示，不跳动）。
String formatClock(int seconds) {
  final s = seconds < 0 ? 0 : seconds;
  final m = s ~/ 60;
  final sec = s % 60;
  return '$m:${sec.toString().padLeft(2, '0')}';
}

/// 秒数 → 人类可读的预计用时（配置页「预计用时」用）。
///
/// 不足 1 分钟说「不到 1 分钟」，避免出现「0 分钟」这种让人困惑的文案。
String formatEstimate(int seconds) {
  if (seconds <= 0) {
    return '不到 1 分钟';
  }
  if (seconds < 60) {
    return '不到 1 分钟';
  }
  final minutes = (seconds / 60).round();
  if (minutes < 60) {
    return '约 $minutes 分钟';
  }
  final h = minutes ~/ 60;
  final m = minutes % 60;
  return m == 0 ? '约 $h 小时' : '约 $h 小时 $m 分钟';
}

/// 分数 → 取整字符串（UI 上不显示小数，避免「83.3333」）。
String formatScore(num score) => score.round().toString();

/// 百分比 → 取整字符串。
String formatPercent(num ratio) => '${(ratio * 100).round()}%';

/// 按「量词 + 数量」拼中文，例：`11 个词`、`2 篇课文`。
String countLabel(int count, String unit) => '$count$unit';

/// 把毫秒格式化为「音频时长」提示（录音条上用）。
String formatAudioMs(int ms) {
  final seconds = ms / 1000.0;
  return '${seconds.toStringAsFixed(1)} 秒';
}

/// 毫秒 → 「3 分 05 秒」/「45 秒」/「0 秒」（场次用时、背诵用时）。
///
/// ⚠️ 项目里原先另有两份**私有**实现（英语结算页 `$m 分 ${ss} 秒`、
/// 语文结算页 `mm:ss`）。这里给出共用的一份，**新代码一律用它**；
/// 那两处暂不动（属别的包，改它们要单独走复核，见 §八）。
String formatDurationMs(int ms) {
  final totalSec = (ms / 1000).round();
  if (totalSec <= 0) {
    return '0 秒';
  }
  final minutes = totalSec ~/ 60;
  final seconds = totalSec % 60;
  if (minutes <= 0) {
    return '$seconds 秒';
  }
  return '$minutes 分 ${seconds.toString().padLeft(2, '0')} 秒';
}
