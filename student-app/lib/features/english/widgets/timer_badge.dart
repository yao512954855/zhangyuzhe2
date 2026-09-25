/// 倒计时徽标 —— F13 / F14 / U12。
///
/// 顶部倒计时常驻；剩余 ≤10 秒变警示色（用**颜色 + 数字变化**，**不用闪烁**，
/// 满足 UI 规范 §5.1 禁闪烁）。不限时显示「不限时」。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/app_theme.dart';
import '../../../core/theme/age_profile.dart';

/// 倒计时徽标。
class TimerBadge extends StatelessWidget {
  const TimerBadge({
    super.key,
    required this.remainingSeconds,
    required this.unlimited,
  });

  final int remainingSeconds;
  final bool unlimited;

  /// 剩余多少秒开始变色（U12）。
  static const int warnThreshold = 10;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    final (Color fg, Color bg, IconData icon, String text) = switch (unlimited) {
      true => (
          extra.textSub,
          extra.card,
          Icons.schedule_rounded,
          '不限时',
        ),
      false => remainingSeconds <= warnThreshold
          ? (
              extra.hint,
              extra.hintLight,
              Icons.hourglass_bottom_rounded,
              '$remainingSeconds 秒',
            )
          : (
              extra.textSub,
              extra.card,
              Icons.schedule_rounded,
              '$remainingSeconds 秒',
            ),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: fg.withValues(alpha: 0.35), width: 0.5),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: profile.caption + 4, color: fg),
          const SizedBox(width: 6),
          Text(text, style: profile.captionStyle.copyWith(color: fg)),
        ],
      ),
    );
  }
}
