/// 语文背诵结算页 —— WP-APP-04 F13 / F14（UI 规范 §7.8 P9）。
///
/// 展示六项指标 + 评级 + 漏字回看：
/// - 六项（F13）：总字数、已背字数、漏字数、提示次数（分档）、总用时、首次发声延迟；
/// - 评级（F13）：优秀 / 良好 / 合格 / 待加强（文字 + 星星双通道，色盲友好）；
/// - 回看（F14）：列出漏掉 / 背错的字及其位置。
///
/// 结算数字以服务端重算为准（契约 §13：客户端上报的 matched_chars 服务端丢弃），
/// 这里只做"展示"，不再算一遍。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/age_profile.dart';
import '../../../core/theme/app_theme.dart';
import '../../../core/widgets/app_button.dart';
import 'recite_controller.dart';

/// 结算页。
class ReciteSettlePage extends ConsumerWidget {
  const ReciteSettlePage({
    super.key,
    required this.settle,
    required this.durationMs,
    this.firstVoiceMs,
    this.hasNext = false,
  });

  /// 服务端重算结果（六项指标 + 评级 + 漏字）。
  final ReciteSettle settle;

  /// 总用时（毫秒），由背诵页计时得到（服务端 settle 不回带，见 D-APP04-03）。
  final int durationMs;

  /// 首次发声延迟（毫秒，可能为空——没发声过）。
  final int? firstVoiceMs;

  /// 是否还有下一篇（一期单篇，固定 false）。
  final bool hasNext;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(title: const Text('背完啦')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
          children: [
            _RatingHeader(grade: settle.grade, score: settle.score),
            const SizedBox(height: AgeProfile.elementGap),

            _MetricsGrid(
              totalChars: settle.totalChars,
              matchedChars: settle.matchedChars,
              missedCount: settle.missedCount,
              hintCount: settle.hintCount,
              durationMs: durationMs,
              firstVoiceMs: firstVoiceMs,
              coveredRatio: settle.coveredRatio,
            ),
            const SizedBox(height: AgeProfile.elementGap),

            _MissedReview(detail: settle.missedDetail),
            const SizedBox(height: AgeProfile.adjacentButtonGap),

            AppButton(
              label: '返回首页',
              icon: Icons.home_rounded,
              onPressed: () => Navigator.of(context).popUntil((r) => r.isFirst),
            ),
            if (hasNext)
              Padding(
                padding: const EdgeInsets.only(top: AgeProfile.adjacentButtonGap),
                child: AppButton(
                  label: '下一篇',
                  kind: AppButtonKind.secondary,
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// 评级头部：文字 + 星星（双通道，色盲也能读懂）。
class _RatingHeader extends StatelessWidget {
  const _RatingHeader({required this.grade, required this.score});

  final String grade;
  final double score;

  int get _stars {
    return switch (grade) {
      '优秀' => 4,
      '良好' => 3,
      '合格' => 2,
      '待加强' => 1,
      _ => 0,
    };
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;
    final filled = _stars > 0 ? extra.success : extra.textFaint;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          children: [
            Text(
              grade.isEmpty ? '已提交' : grade,
              style: profile.pageTitleStyle.copyWith(
                color: palette.primary,
                fontWeight: FontWeight.w500,
              ),
            ),
            const SizedBox(height: 8),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                for (var i = 0; i < 4; i++)
                  Icon(
                    i < _stars ? Icons.star_rounded : Icons.star_outline_rounded,
                    color: i < _stars ? filled : extra.divider,
                    size: profile.caption + 14,
                  ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              '得分 ${score.toStringAsFixed(0)}',
              style: profile.captionStyle.copyWith(color: extra.textSub),
            ),
          ],
        ),
      ),
    );
  }
}

/// 六项指标网格。
class _MetricsGrid extends StatelessWidget {
  const _MetricsGrid({
    required this.totalChars,
    required this.matchedChars,
    required this.missedCount,
    required this.hintCount,
    required this.durationMs,
    required this.firstVoiceMs,
    required this.coveredRatio,
  });

  final int totalChars;
  final int matchedChars;
  final int missedCount;
  final Map<int, int> hintCount;
  final int durationMs;
  final int? firstVoiceMs;
  final double coveredRatio;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    final h1 = hintCount[1] ?? 0;
    final h2 = hintCount[2] ?? 0;
    final h3 = hintCount[3] ?? 0;
    final hintText = (h1 + h2 + h3) == 0
        ? '未使用'
        : '一档 $h1 · 二档 $h2 · 三档 $h3';

    final tiles = <_MetricTile>[
      _MetricTile(label: '总字数', value: '$totalChars'),
      _MetricTile(label: '已背字数', value: '$matchedChars'),
      _MetricTile(label: '漏字数', value: '$missedCount'),
      _MetricTile(label: '提示次数', value: hintText, small: true),
      _MetricTile(label: '总用时', value: _fmtDuration(durationMs)),
      _MetricTile(
        label: '首次发声延迟',
        value: firstVoiceMs == null ? '未发声' : _fmtDuration(firstVoiceMs!),
      ),
    ];

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('这一遍的成绩', style: profile.cardTitleStyle),
            const SizedBox(height: 12),
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                for (final t in tiles)
                  SizedBox(
                    width: (MediaQuery.of(context).size.width -
                            AgeProfile.screenEdgeInset * 2 -
                            16 * 2 -
                            12) /
                        2,
                    child: t,
                  ),
              ],
            ),
            const SizedBox(height: 14),
            // 覆盖率进度条（颜色表达，不闪烁）。
            Text('覆盖率 ${(coveredRatio * 100).toStringAsFixed(0)}%',
                style: profile.captionStyle.copyWith(color: extra.textSub)),
            const SizedBox(height: 6),
            LinearProgressIndicator(
              value: coveredRatio.clamp(0.0, 1.0),
              minHeight: 8,
              borderRadius: BorderRadius.circular(4),
              color: extra.success,
              backgroundColor: extra.divider,
            ),
          ],
        ),
      ),
    );
  }

  static String _fmtDuration(int ms) {
    final total = (ms / 1000).round();
    final m = total ~/ 60;
    final s = total % 60;
    return '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }
}

class _MetricTile extends StatelessWidget {
  const _MetricTile({required this.label, required this.value, this.small = false});

  final String label;
  final String value;
  final bool small;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: profile.captionStyle.copyWith(color: extra.textFaint)),
        const SizedBox(height: 4),
        Text(
          value,
          style: (small ? profile.bodyStyle : profile.cardTitleStyle)
              .copyWith(color: Theme.of(context).colorScheme.onSurface),
        ),
      ],
    );
  }
}

/// 漏字回看（F14）。
class _MissedReview extends StatelessWidget {
  const _MissedReview({required this.detail});

  final List<ReciteMissedChar> detail;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('回看没背出来的地方', style: profile.cardTitleStyle),
            const SizedBox(height: 10),
            if (detail.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  '这一遍没有漏字，真棒！',
                  style: profile.bodyStyle.copyWith(color: extra.success),
                ),
              )
            else
              for (final m in detail)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Row(
                    children: [
                      Icon(Icons.radio_button_unchecked_rounded,
                          size: profile.caption + 4, color: extra.error),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '第 ${m.pos + 1} 个字：${m.char ?? '？'}',
                          style: profile.bodyStyle.copyWith(color: Theme.of(context).colorScheme.onSurface),
                        ),
                      ),
                    ],
                  ),
                ),
          ],
        ),
      ),
    );
  }
}
