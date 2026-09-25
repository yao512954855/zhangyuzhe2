/// 首页单元卡 —— UI 规范 P4（卡片式、数字型进度、四种状态标记、快捷开始）。
///
/// 卡片的两个点击区**必须分开**（F6 与 F14 是两个不同动作，学生很容易点错）：
/// - 点卡片空白处 → 进单元详情（看词条与各自状态）；
/// - 点卡片里的「开始练习」→ 用默认组合**直接开练**，不进配置页。
///
/// 进度条一律用主色，**不用红绿** —— UI 规范 §7.4 明说
/// "红绿是股票习惯，学习场景用中性色更稳"。
library;

import 'package:flutter/material.dart';

import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import 'mastery_badge.dart';
import 'models.dart';
import 'time_format.dart';

class UnitCard extends StatelessWidget {
  const UnitCard({
    super.key,
    required this.unit,
    required this.onTap,
    required this.onStart,
    this.now,
  });

  final UnitItem unit;

  /// 点卡片进详情（F14）。
  final VoidCallback onTap;

  /// 点「开始练习」按默认组合开练（F6）。
  final VoidCallback onStart;

  /// 可注入的当前时间（让"上次：今天/昨天"能被单测钉死，不受运行时刻影响）。
  final DateTime? now;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final lastPractice = formatLastPractice(unit.lastPracticeAt, now: now);

    return Semantics(
      button: true,
      label: '${unit.subject.label} ${unit.name}，进度 ${unit.progressText}，'
          '${unit.status.label}。点卡片看词条，点下面的按钮直接开始练习',
      excludeSemantics: true,
      child: Container(
        decoration: BoxDecoration(
          color: extra.card,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: extra.divider, width: 0.5),
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: BorderRadius.circular(14),
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Text(
                          '${unit.subject.label} ${unit.name}',
                          style: profile.cardTitleStyle,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      const SizedBox(width: 8),
                      MasteryBadge(status: unit.status),
                    ],
                  ),
                  const SizedBox(height: 12),
                  Row(
                    children: [
                      Expanded(child: _ProgressBar(ratio: unit.progressRatio)),
                      const SizedBox(width: 12),
                      Text(
                        unit.progressText,
                        style: profile.bodyStyle.copyWith(
                          color: extra.textSub,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  // 语文卡要有"课文数"而不是"词条数"（U11）
                  Text(
                    unit.countLabel,
                    style: profile.captionStyle.copyWith(color: extra.textFaint),
                  ),
                  const SizedBox(height: 6),
                  UnitStatLine(unit: unit),
                  if (lastPractice != null) ...[
                    const SizedBox(height: 8),
                    Text(
                      '上次：$lastPractice',
                      style: profile.captionStyle.copyWith(color: extra.textFaint),
                    ),
                  ],
                  const SizedBox(height: 12),
                  AppButton(
                    label: '开始练习',
                    kind: AppButtonKind.secondary,
                    icon: Icons.play_arrow_rounded,
                    onPressed: onStart,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _ProgressBar extends StatelessWidget {
  const _ProgressBar({required this.ratio});

  final double ratio;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    return Semantics(
      label: '进度 ${(ratio * 100).round()}%',
      excludeSemantics: true,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: LinearProgressIndicator(
          value: ratio,
          minHeight: 8,
          backgroundColor: extra.divider,
          valueColor: AlwaysStoppedAnimation<Color>(
            Theme.of(context).colorScheme.primary,
          ),
        ),
      ),
    );
  }
}
