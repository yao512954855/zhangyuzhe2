/// 我的统计 —— WP-APP-05 F3（U4）。
///
/// 渲染**真能拿到**的进度数据：`GET /api/me/progress` 的
/// 正确率 / 已掌握词条数 / 连续练习天数（契约 §8.4）。
///
/// 关于「正确率趋势」：学生端**没有**历史正确率序列接口
/// （`stats_service` 的 `trend` 是**班级级**、供管理者用，契约未给学生开放），
/// 所以这里**不伪造时间序列**——趋势图在暂无数据时显示诚实的占位文案，
/// 等后端补齐个人历史接口再填充（见交付说明偏离项）。U10 的「空数据 / 单点不崩」
/// 由 [TrendChart] 本身保证，并有单元测试覆盖。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/feedback.dart';
import '../home/models.dart';
import 'my_records_page.dart' show mineProgressProvider;
import 'widgets/trend_chart.dart';

/// 「我的统计」页。
class MyStatsPage extends ConsumerWidget {
  const MyStatsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final progress = ref.watch(mineProgressProvider);
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(title: Text('我的统计', style: profile.pageTitleStyle)),
      body: progress.when(
        loading: () => const LoadingView(),
        error: (e, _) => ErrorView(
          message: e is ApiException ? e.displayText : '暂时拿不到统计数据',
          onRetry: () => ref.invalidate(mineProgressProvider),
        ),
        data: (p) => ListView(
          padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
          children: [
            _Headline(progress: p, profile: profile, extra: extra),
            const SizedBox(height: AgeProfile.elementGap),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('正确率趋势', style: profile.cardTitleStyle),
                    const SizedBox(height: 8),
                    TrendChart(
                      // ⚠️ 暂无个人历史接口，传空列表 → 显示诚实占位（不伪造曲线）。
                      points: const [],
                      lineColor: extra.hint,
                      axisColor: extra.divider,
                      labelColor: extra.textSub,
                      hintColor: extra.textFaint,
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: AgeProfile.elementGap),
            _NumberGrid(progress: p, profile: profile, extra: extra),
          ],
        ),
      ),
    );
  }
}

/// 正确率大数字。
class _Headline extends StatelessWidget {
  const _Headline({
    required this.progress,
    required this.profile,
    required this.extra,
  });

  final ProgressOverview progress;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Text(
                '${(progress.accuracy * 100).round()}%',
                style: profile.wordStyle.copyWith(
                  fontSize: profile.wordBig + 8,
                  color: extra.hint,
                ),
              ),
              const SizedBox(height: 4),
              Text('总体正确率', style: profile.captionStyle.copyWith(color: extra.textSub)),
            ],
          ),
        ),
      );
}

/// F3 真实数字：已掌握词条 / 连续练习天数 / 累计练习。
class _NumberGrid extends StatelessWidget {
  const _NumberGrid({
    required this.progress,
    required this.profile,
    required this.extra,
  });

  final ProgressOverview progress;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              _Cell(
                value: '${progress.masteredWords}',
                label: '已掌握词条',
                profile: profile,
                extra: extra,
              ),
              _Cell(
                value: '${progress.streakDays}',
                label: '连续练习(天)',
                profile: profile,
                extra: extra,
              ),
              _Cell(
                value: '${progress.practicedWords}',
                label: '累计练习',
                profile: profile,
                extra: extra,
              ),
            ],
          ),
        ),
      );
}

class _Cell extends StatelessWidget {
  const _Cell({
    required this.value,
    required this.label,
    required this.profile,
    required this.extra,
  });

  final String value;
  final String label;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) => Expanded(
        child: Column(
          children: [
            Text(value, style: profile.wordStyle.copyWith(color: Theme.of(context).colorScheme.onSurface)),
            const SizedBox(height: 4),
            Text(label, style: profile.captionStyle.copyWith(color: extra.textSub)),
          ],
        ),
      );
}
