/// 练习预估 —— F12「配置页顶部实时显示本次题量与预计用时」。
///
/// **纯函数**（不依赖 Widget / 网络），所以能被单测逐个档位钉死 ——
/// 预估是不可逆的体验损失：说"约 3 分钟"结果练了 10 分钟，
/// 学生会以为是自己太慢，而不是系统算错了。
///
/// 基准参数的来源与量级（**是估算，不是精确值**）：
/// - 英语「看中文说英文」= 看题 4s + 说词 6s + 拼读 8s ≈ **18s/题**
///   （拼读是逐字母念，比整词说更慢；`require_spell` 默认开启）
/// - 英语「看英文说中文」= **10s/题**（只要认识就能说）
/// - 英语「混合」= 两者平均 **14s/题**
/// - 语文背诵 = **0.6s/字** + 每题起手 **5s**
///   （小学生朗读约 150–200 字/分钟 ≈ 0.3–0.4s/字，背诵要回忆，取 1.5–2 倍）
library;

import 'package:flutter/foundation.dart';

import '../../core/util/format.dart';
import '../home/models.dart';
import 'practice_strategy.dart';

/// 语文一篇课文的默认背诵字数（拿不到课文列表时的兜底）。
const int kDefaultReciteChars = 200;

/// 英语各模式的单题秒数。
const int kCn2EnSeconds = 18;
const int kEn2CnSeconds = 10;
const int kMixedSeconds = 14;

/// 语文：每个字几秒 + 每题起手。
const double kReciteSecondsPerChar = 0.6;
const int kReciteWarmUpSeconds = 5;

/// 一次练习的预估结果。
@immutable
class PracticeEstimate {
  const PracticeEstimate({required this.itemCount, required this.seconds});

  /// 本次题量。
  final int itemCount;

  /// 预计用时（秒）。
  final int seconds;

  /// 「本次 20 题 · 约 3 分钟」（UI 规范 P5 的预估条文案）。
  String get summary {
    if (itemCount <= 0) {
      return '这个范围里没有题目';
    }
    return '本次 $itemCount 题 · ${formatEstimate(seconds)}';
  }

  @override
  bool operator ==(Object other) =>
      other is PracticeEstimate && other.itemCount == itemCount && other.seconds == seconds;

  @override
  int get hashCode => Object.hash(itemCount, seconds);
}

/// 算预估。
///
/// [texts] 只在语文「选课文」时提供（那时页面本来就拉了课文列表，
/// 拿得到每篇的真实背诵字数 `recite_scope_chars`）；不给则用
/// [kDefaultReciteChars] 兜底。
PracticeEstimate estimatePractice(PracticeConfig config, {List<TextBrief>? texts}) {
  final count = config.itemCount;
  if (count <= 0) {
    return const PracticeEstimate(itemCount: 0, seconds: 0);
  }

  final limit = config.timeLimit;

  // 整组限时：时间到就结束，实际用时**就是限时本身**，
  // 不需要再估（估出来反而会与倒计时对不上）。
  if (limit.kind == TimeLimitKind.wholeGroup) {
    return PracticeEstimate(itemCount: count, seconds: limit.seconds);
  }

  final perItem = config.isEnglish
      ? _englishPerItemSeconds(config.mode)
      : _chinesePerItemSeconds(config, texts);

  final capped = limit.kind == TimeLimitKind.perQuestion && limit.seconds < perItem
      ? limit.seconds
      : perItem;

  return PracticeEstimate(itemCount: count, seconds: capped * count);
}

int _englishPerItemSeconds(EnglishMode mode) => switch (mode) {
      EnglishMode.cn2en => kCn2EnSeconds,
      EnglishMode.en2cn => kEn2CnSeconds,
      EnglishMode.mixed => kMixedSeconds,
    };

/// 语文：本次选中课文的平均每篇秒数。
///
/// 拿得到课文列表就按真实字数逐篇算，拿不到就按默认字数。
int _chinesePerItemSeconds(PracticeConfig config, List<TextBrief>? texts) {
  final picked = texts;
  if (picked == null || picked.isEmpty) {
    return (kDefaultReciteChars * kReciteSecondsPerChar).round() + kReciteWarmUpSeconds;
  }

  final List<TextBrief> selected;
  if (config.scope == PracticeScope.lesson && config.selectedTextIds.isNotEmpty) {
    selected = picked.where((t) => config.selectedTextIds.contains(t.id)).toList(growable: false);
  } else {
    selected = picked;
  }
  if (selected.isEmpty) {
    return (kDefaultReciteChars * kReciteSecondsPerChar).round() + kReciteWarmUpSeconds;
  }

  final totalChars = selected.fold<int>(
    0,
    (sum, text) => sum + (text.reciteScopeChars > 0 ? text.reciteScopeChars : kDefaultReciteChars),
  );
  final average = totalChars / selected.length;
  return (average * kReciteSecondsPerChar).round() + kReciteWarmUpSeconds;
}

/// 语文课文按背诵字数算的单篇秒数（配置页展示每篇预计用时用）。
int reciteSecondsFor(TextBrief text) =>
    ((text.reciteScopeChars > 0 ? text.reciteScopeChars : kDefaultReciteChars) *
                kReciteSecondsPerChar)
            .round() +
        kReciteWarmUpSeconds;
