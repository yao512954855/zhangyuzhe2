/// 我的统计 —— WP-APP-05 F3（U4）+ WP-2A（个人正确率趋势，二期恢复）。
///
/// 渲染**真能拿到**的进度数据：`GET /api/me/progress` 的
/// 正确率 / 已掌握词条数 / 连续练习天数（契约 §8.4），以及二期新增的
/// `GET /api/me/accuracy-trend` 逐日正确率趋势（WP-2A）。
///
/// **关于「正确率趋势」**：一期因学生端没有个人历史正确率序列接口而随组件一起摘除
/// （姚工 2026-09-25 裁决）。二期 WP-2A 补了 `GET /api/me/accuracy-trend`，
/// 这里把趋势卡片**恢复**回去 —— 见 [mineAccuracyTrendProvider] 与 [TrendChart]。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/feedback.dart';
import '../home/models.dart';
import 'my_records_page.dart' show mineProgressProvider;
import 'trend_chart.dart';

/// 个人正确率趋势（WP-2A）。
///
/// 走生产代码 `ApiClient`，由页面 watch；失败给中文错误，不静默。
/// 数据形状 `{points:[{date,accuracy,answered,correct}]}`（契约 §15.5），
/// 正确率已由后端按「剔除 empty」的口径算好，客户端不重算。
final mineAccuracyTrendProvider = FutureProvider<AccuracyTrend>((ref) async {
  final data = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('/api/me/accuracy-trend');
  return AccuracyTrend.fromJson(data ?? const <String, dynamic>{});
});

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
            _NumberGrid(progress: p, profile: profile, extra: extra),
            const SizedBox(height: AgeProfile.elementGap),
            _TrendCard(profile: profile, extra: extra),
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

/// WP-2A 趋势卡片：标题 + 近 30 天正确率折线图（[TrendChart]）。
///
/// 独立 watch [mineAccuracyTrendProvider]，进度块与趋势块各自加载/报错互不影响。
class _TrendCard extends ConsumerWidget {
  const _TrendCard({required this.profile, required this.extra});

  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final trend = ref.watch(mineAccuracyTrendProvider);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Text('正确率趋势', style: profile.cardTitleStyle),
                const Spacer(),
                trend.when(
                  loading: () => const SizedBox.shrink(),
                  error: (_, __) => const SizedBox.shrink(),
                  data: (t) => Text(
                    '最近 ${(t.latestAccuracy * 100).round()}%',
                    style: profile.captionStyle.copyWith(color: extra.textSub),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            trend.when(
              loading: () => const SizedBox(
                height: 200,
                child: LoadingView(),
              ),
              error: (e, _) => ErrorView(
                message: e is ApiException ? e.displayText : '暂时拿不到趋势数据',
                onRetry: () => ref.invalidate(mineAccuracyTrendProvider),
              ),
              data: (t) => TrendChart(points: t.points),
            ),
          ],
        ),
      ),
    );
  }
}
