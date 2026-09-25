/// 背诵计时器 —— WP-APP-04 F11 / F14（UI 规范 §5.8、§7.7）。
///
/// 两种模式：
/// - **正计时**（无限时）：显示已用时间 `mm:ss`；
/// - **倒计时**（整篇限时）：显示剩余时间，剩余 ≤10 秒变警示色 —— **不用闪烁**
///   （UI 规范 §2.5 禁闪烁，靠颜色 + 数字变化表达紧迫）。
///
/// 本组件只负责"显示"，不持有 [Timer]：跳动由页面用 `Timer.periodic` 驱动，
/// 把最新的 [elapsed] 传进来即可。这样组件可单测（直接喂 Duration 断言文案）。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/age_profile.dart';
import '../../../core/theme/app_theme.dart';

/// 背诵计时器。
class ReciteTimer extends StatelessWidget {
  const ReciteTimer({
    super.key,
    required this.elapsed,
    this.limit,
  });

  /// 已用时间（由页面按秒累加）。
  final Duration elapsed;

  /// 整篇限时（毫秒）。为 null 表示不限时（纯正计时）。
  final Duration? limit;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    final bool countdown = limit != null;
    final Duration remaining = countdown ? (limit! - elapsed) : Duration.zero;
    final bool negative = countdown && remaining.isNegative;
    final Duration shown = countdown
        ? (negative ? Duration.zero : remaining)
        : elapsed;

    // 剩余 ≤10 秒：警示色（不闪烁）。
    final bool warning = countdown && !negative && remaining <= const Duration(seconds: 10);
    final Color textColor = warning ? extra.error : extra.textSub;

    final String label = countdown ? '剩余' : '已用';

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(
          countdown ? Icons.hourglass_bottom_rounded : Icons.schedule_rounded,
          size: profile.caption + 4,
          color: textColor,
        ),
        const SizedBox(width: 6),
        Text(
          '$label ${_format(shown)}',
          style: profile.captionStyle.copyWith(
            color: textColor,
            fontWeight: warning ? FontWeight.w500 : FontWeight.w400,
          ),
        ),
      ],
    );
  }

  static String _format(Duration d) {
    final total = d.inSeconds.abs();
    final m = total ~/ 60;
    final s = total % 60;
    return '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }
}
