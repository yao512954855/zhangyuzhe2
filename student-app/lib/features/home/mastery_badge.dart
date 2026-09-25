/// 掌握状态标记 —— UI 规范 §7.4「状态图标设计」（**四态图形各不相同**）。
///
/// 为什么四个图形必须长得不一样、而不是用四种颜色：
/// §2.5 配色禁忌明确"不能只靠颜色区分状态" —— 红绿色盲在男生里约占 8%，
/// 而本系统的使用者正是 6–15 岁学生。所以：
/// **形状是第一通道（空心圆 / 感叹号 / 对勾 / 实心星），颜色只是第二通道。**
///
/// 本文件归 WP-APP-02；APP-03/04/05 若要复用请 `import` 使用，
/// **不要另写一份**（否则四处口径漂移），修改请回到本文件。
library;

import 'package:flutter/material.dart';

import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import 'models.dart';

/// 状态 → (图标, 取色) 的唯一映射表。
///
/// 抽成函数而不是写在 `build` 里，是为了单测能直接断言"四态互不相同"。
({IconData icon, Color Function(AppExtraColors) color}) masteryVisual(MasteryStatus status) =>
    switch (status) {
      MasteryStatus.notStarted => (
          icon: Icons.radio_button_unchecked_rounded,
          color: (extra) => extra.textFaint,
        ),
      MasteryStatus.practicedWithError => (
          icon: Icons.error_outline_rounded,
          color: (extra) => extra.error,
        ),
      MasteryStatus.practicedOk => (
          icon: Icons.check_circle_outline_rounded,
          color: (extra) => extra.success,
        ),
      MasteryStatus.mastered => (
          icon: Icons.star_rounded,
          color: (extra) => extra.hint,
        ),
    };

/// 一枚状态标记（纯图标，或图标 + 文字）。
class MasteryBadge extends StatelessWidget {
  const MasteryBadge({
    super.key,
    required this.status,
    this.size,
    this.showLabel = false,
  });

  final MasteryStatus status;

  /// 边长，默认跟分龄档位的图标尺寸走。
  final double? size;

  /// 是否在图标后带上文字（单元详情页用；首页卡片上只用图标，省横向空间）。
  final bool showLabel;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);
    final visual = masteryVisual(status);
    final side = size ?? (profile.caption + 6);

    // 无障碍：图标本身没有语义，必须显式告诉读屏"这是哪个状态"
    return Semantics(
      label: '掌握状态：${status.label}',
      excludeSemantics: true,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(visual.icon, size: side, color: visual.color(extra)),
          if (showLabel) ...[
            const SizedBox(width: 6),
            Text(
              status.label,
              style: profile.captionStyle.copyWith(color: visual.color(extra)),
            ),
          ],
        ],
      ),
    );
  }
}

/// 首页单元卡上的「✓已练 ✗有错 ★已掌握 正确率」一行。
///
/// ⚠️ **「✗N」在后端补 `with_error` 之前不显示**（见 `models.dart` 的注释与
/// `docs/dev/02` §八 偏离 #44）：契约 §8.3 没下发这个字段，
/// 拿 `word_total - practiced` 之类的算法硬凑只会是**错的数字**。
/// 宁可少显示一项，也不能给学生一个假的数。
class UnitStatLine extends StatelessWidget {
  const UnitStatLine({super.key, required this.unit});

  final UnitItem unit;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);

    TextStyle stat(Color color) => profile.captionStyle.copyWith(color: color);

    final chips = <Widget>[];

    if (unit.subject == Subject.english) {
      chips.add(_Chip(
        icon: Icons.check_circle_outline_rounded,
        color: extra.success,
        text: '${unit.practiced}',
        style: stat(extra.success),
      ));
      final withError = unit.withError;
      if (withError != null) {
        chips.add(_Chip(
          icon: Icons.error_outline_rounded,
          color: extra.error,
          text: '$withError',
          style: stat(extra.error),
        ));
      }
      chips.add(_Chip(
        icon: Icons.star_rounded,
        color: extra.hint,
        text: '${unit.mastered}',
        style: stat(extra.hint),
      ));
      if (unit.practiced > 0) {
        chips.add(Text('正确率 ${(unit.accuracy * 100).round()}%', style: stat(extra.textSub)));
      }
    } else {
      // 语文：课文级没有"正确率"概念，显示最近一篇的评级（UI 规范 P4 卡片）
      final lastGrade = unit.lastGrade;
      if (lastGrade != null && lastGrade.isNotEmpty) {
        chips.add(Text('最近一篇：$lastGrade', style: stat(extra.textSub)));
      } else {
        chips.add(Text('还没背过', style: stat(extra.textFaint)));
      }
    }

    if (chips.isEmpty) {
      return const SizedBox.shrink();
    }

    return Wrap(
      spacing: 12,
      runSpacing: 4,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: chips,
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({
    required this.icon,
    required this.color,
    required this.text,
    required this.style,
  });

  final IconData icon;
  final Color color;
  final String text;
  final TextStyle style;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: style.fontSize! + 2, color: color),
        const SizedBox(width: 4),
        Text(text, style: style),
      ],
    );
  }
}
