/// 预估条 —— UI 规范 P5「顶部实时显示本次 N 题 · 约 M 分钟」。
///
/// 它是一条**常驻**信息（不随滚动消失），因为它回答的是学生最关心的问题：
/// "还要多久才能玩"。放在「开始练习」按钮正上方，点之前一定看得到。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/age_profile.dart';
import '../../../core/theme/app_theme.dart';
import '../practice_estimate.dart';

class EstimateBar extends StatelessWidget {
  const EstimateBar({super.key, required this.estimate, this.blockReason});

  final PracticeEstimate estimate;

  /// 不能开始的原因（有值时优先显示原因，帮学生知道该改哪里）。
  final String? blockReason;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;

    final blocked = blockReason != null;
    final text = blocked ? blockReason! : estimate.summary;
    final fg = blocked ? extra.error : palette.primary;
    final bg = blocked ? extra.errorLight : palette.primaryContainer;

    return Semantics(
      liveRegion: true,
      label: text,
      excludeSemantics: true,
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: fg.withValues(alpha: 0.35), width: 0.5),
        ),
        child: Row(
          children: [
            Icon(
              blocked ? Icons.info_outline_rounded : Icons.schedule_rounded,
              size: profile.caption + 8,
              color: fg,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                text,
                style: profile.bodyStyle.copyWith(color: fg, fontWeight: FontWeight.w500),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
