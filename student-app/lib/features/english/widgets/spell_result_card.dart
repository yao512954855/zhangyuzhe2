/// 逐字母回显卡片 —— F5 / P7。
///
/// 按服务端 `diff` 渲染（客户端**只渲染**，对齐算法在服务端，契约 §13）：
/// - `match`   正常显示
/// - `wrong`   高亮差异位，显示学生读成的字母（颜色 + 图标双通道）
/// - `missing` 占位框 + 「这里没听到」
/// - `extra`   标记多余字母
/// 同时展示**原始识别文本**（如「see ell aye ess ess」），让学生理解机器听到了什么。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/app_theme.dart';
import '../../../core/theme/age_profile.dart';
import '../practice_controller.dart';

/// 逐字母回显卡片。
class SpellResultCard extends StatelessWidget {
  const SpellResultCard({
    super.key,
    required this.diff,
    this.rawText,
    this.correctTitle = '正确拼读',
    this.yoursTitle = '你拼读的',
  });

  final List<AnswerDiffCell> diff;
  final String? rawText;
  final String correctTitle;
  final String yoursTitle;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    // 找第一个差异位，做一句话说明（精确到第几位）。
    final firstWrong = diff.where((c) => c.isWrong).firstOrNull;
    final firstMissing = diff.where((c) => c.isMissing).firstOrNull;
    final caption = switch (firstWrong) {
      AnswerDiffCell c => '第 ${c.pos + 1} 位听成了「${c.actual}」，应该是「${c.expected}」',
      _ => switch (firstMissing) {
          AnswerDiffCell c => '第 ${c.pos + 1} 位没听到（应该是「${c.expected}」）',
          _ => null,
        },
    };

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _RowLabel(profile.cardTitleStyle, correctTitle),
        const SizedBox(height: 8),
        _LettersRow(diff: diff, showActual: false),
        const SizedBox(height: 16),
        _RowLabel(profile.cardTitleStyle, yoursTitle),
        const SizedBox(height: 8),
        _LettersRow(diff: diff, showActual: true),
        if (caption != null) ...[
          const SizedBox(height: 12),
          // 颜色 + 图标双通道，不只靠颜色（UI 规范 §5.2）。
          Row(
            children: [
              Icon(Icons.info_rounded, size: profile.caption + 4, color: extra.hint),
              const SizedBox(width: 8),
              Expanded(
                child: Text(caption, style: profile.captionStyle.copyWith(color: extra.textSub)),
              ),
            ],
          ),
        ],
        if (rawText != null && rawText!.isNotEmpty) ...[
          const SizedBox(height: 12),
          Text('我听到的是：', style: profile.captionStyle.copyWith(color: extra.textFaint)),
          const SizedBox(height: 4),
          Text(rawText!, style: profile.bodyStyle.copyWith(color: extra.textSub)),
        ],
      ],
    );
  }
}

class _RowLabel extends StatelessWidget {
  const _RowLabel(this.style, this.text);
  final TextStyle style;
  final String text;
  @override
  Widget build(BuildContext context) => Text(text, style: style);
}

/// 一行字母：根据 `showActual` 决定显示「标准答案」还是「学生读出」。
class _LettersRow extends StatelessWidget {
  const _LettersRow({required this.diff, required this.showActual});

  final List<AnswerDiffCell> diff;
  final bool showActual;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: diff.map((cell) {
        final isExtra = cell.isExtra;
        final isMissing = cell.isMissing;
        final isWrong = cell.isWrong;

        // 该渲染的字符。
        final ch = showActual ? cell.actual : cell.expected;

        // extra 在「正确拼读」行不出现（没有标准字母）。
        if (isExtra && !showActual) {
          return const SizedBox.shrink();
        }

        final (Color fg, Color bg) = switch ((showActual, isWrong, isMissing, isExtra)) {
          (true, true, false, false) => (extra.error, extra.errorLight),
          (true, false, true, false) => (extra.textFaint, extra.card),
          (true, false, false, true) => (extra.hint, extra.hintLight),
          _ => (Theme.of(context).colorScheme.onSurface, extra.card),
        };

        if (isMissing && showActual) {
          // 占位框 + 「没听到」提示（虚线框，不闪）。
          return Container(
            width: 40,
            height: 48,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              border: Border.all(color: extra.textFaint, width: 1.5, style: BorderStyle.solid),
              borderRadius: BorderRadius.circular(8),
              color: extra.card,
            ),
            child: Icon(Icons.block_rounded, size: 18, color: extra.textFaint),
          );
        }

        return Container(
          width: 40,
          height: 48,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: bg,
            borderRadius: BorderRadius.circular(8),
            border: isWrong
                ? Border.all(color: extra.error, width: 1.5)
                : isExtra
                    ? Border.all(color: extra.hint, width: 1.5, style: BorderStyle.solid)
                    : null,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                (ch.isEmpty ? '·' : ch).toUpperCase(),
                style: profile.bodyStyle.copyWith(
                  color: fg,
                  fontWeight: FontWeight.w500,
                  fontSize: profile.body + 2,
                ),
              ),
              if (isExtra) ...[
                const SizedBox(width: 2),
                Icon(Icons.add_circle_rounded, size: 14, color: extra.hint),
              ],
            ],
          ),
        );
      }).toList(growable: false),
    );
  }
}
