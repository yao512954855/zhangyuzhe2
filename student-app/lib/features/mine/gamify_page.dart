/// 游戏化激励页 —— WP-2K（二期契约 §15.2：`GET /api/me/gamify`）。
///
/// 渲染**真能拿到**的激励数据：积分、连续练习天数、徽章墙。
/// 所有数字与徽章达成状态由后端按现有数据推导（契约禁止"假数据/编造"），
/// 客户端只渲染、不重算。
///
/// 徽章墙展示**全部徽章目录项**：已达成项带"已点亮"标识与获得时间，
/// 未达成项显示"未解锁"占位，给学生明确的目标感。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/feedback.dart';

/// 单个徽章（契约 §15.2：`{code, name, earned, earned_at?}`）。
class BadgeItem {
  const BadgeItem({
    required this.code,
    required this.name,
    required this.earned,
    this.earnedAt,
  });

  factory BadgeItem.fromJson(Map<String, dynamic> json) => BadgeItem(
        code: json['code'] as String? ?? '',
        name: json['name'] as String? ?? '',
        earned: json['earned'] as bool? ?? false,
        earnedAt: json['earned_at'] as String?,
      );

  final String code;
  final String name;
  final bool earned;
  final String? earnedAt;
}

/// 游戏化数据（契约 §15.2：`{points, streak_days, badges:[...]}`）。
class GamifyData {
  const GamifyData({
    required this.points,
    required this.streakDays,
    required this.badges,
  });

  factory GamifyData.fromJson(Map<String, dynamic> json) => GamifyData(
        points: json['points'] as int? ?? 0,
        streakDays: json['streak_days'] as int? ?? 0,
        badges: (json['badges'] as List? ?? const <dynamic>[])
            .map((e) => BadgeItem.fromJson(e as Map<String, dynamic>))
            .toList(),
      );

  final int points;
  final int streakDays;
  final List<BadgeItem> badges;
}

/// `GET /api/me/gamify` 的 FutureProvider（走生产 `ApiClient`，由页面 watch）。
///
/// 不启用 Riverpod 自动重试：错误态由 [GamifyPage] 的「再试一次」按钮显式触发
/// `ref.invalidate`，静默重试会掩盖业务错误且与显式重试控件打架。
final mineGamifyProvider = FutureProvider<GamifyData>((ref) async {
  final data = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('/api/me/gamify');
  return GamifyData.fromJson(data ?? const <String, dynamic>{});
}, retry: (_, _) => null);

/// 我的成就页（积分 / 连续天数 / 徽章墙）。
class GamifyPage extends ConsumerWidget {
  const GamifyPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final gamify = ref.watch(mineGamifyProvider);
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(title: Text('我的成就', style: profile.pageTitleStyle)),
      body: gamify.when(
        loading: () => const LoadingView(),
        error: (e, _) => ErrorView(
          message: e is ApiException ? e.displayText : '暂时拿不到成就数据',
          onRetry: () => ref.invalidate(mineGamifyProvider),
        ),
        data: (g) => ListView(
          padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
          children: [
            _ScoreBoard(g: g, profile: profile, extra: extra),
            const SizedBox(height: AgeProfile.elementGap),
            _BadgeWall(g: g, profile: profile, extra: extra),
          ],
        ),
      ),
    );
  }
}

/// 积分 + 连续天数的大数字卡片。
class _ScoreBoard extends StatelessWidget {
  const _ScoreBoard({
    required this.g,
    required this.profile,
    required this.extra,
  });

  final GamifyData g;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    Text(
                      '${g.points}',
                      style: profile.wordStyle.copyWith(
                        fontSize: profile.wordBig + 8,
                        color: extra.hint,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text('积分', style: profile.captionStyle.copyWith(color: extra.textSub)),
                  ],
                ),
              ),
              Container(
                width: 1,
                height: 48,
                color: Theme.of(context).dividerColor,
              ),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    Text(
                      '${g.streakDays}',
                      style: profile.wordStyle.copyWith(
                        fontSize: profile.wordBig + 8,
                        color: Theme.of(context).colorScheme.onSurface,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text('连续练习(天)', style: profile.captionStyle.copyWith(color: extra.textSub)),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
}

/// 徽章墙：展示全部徽章目录项，已点亮 / 未解锁双态。
class _BadgeWall extends StatelessWidget {
  const _BadgeWall({
    required this.g,
    required this.profile,
    required this.extra,
  });

  final GamifyData g;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('徽章墙', style: profile.cardTitleStyle),
              const SizedBox(height: 12),
              if (g.badges.isEmpty)
                Text('还没有徽章，去练习赢取第一枚吧！',
                    style: profile.captionStyle.copyWith(color: extra.textSub))
              else
                GridView.count(
                  crossAxisCount: 3,
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  mainAxisSpacing: 12,
                  crossAxisSpacing: 12,
                  childAspectRatio: 0.9,
                  children: [for (final b in g.badges) _BadgeTile(badge: b, profile: profile, extra: extra)],
                ),
            ],
          ),
        ),
      );
}

/// 单枚徽章瓦片。
class _BadgeTile extends StatelessWidget {
  const _BadgeTile({
    required this.badge,
    required this.profile,
    required this.extra,
  });

  final BadgeItem badge;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) => Container(
        decoration: BoxDecoration(
          border: Border.all(
            color: badge.earned
                ? extra.hint
                : Theme.of(context).dividerColor,
            width: badge.earned ? 2 : 1,
          ),
          borderRadius: BorderRadius.circular(12),
          color: badge.earned
              ? extra.hint.withOpacity(0.12)
              : Theme.of(context).colorScheme.surface,
        ),
        padding: const EdgeInsets.all(8),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              badge.earned ? Icons.emoji_events_rounded : Icons.lock_outline_rounded,
              size: 28,
              color: badge.earned ? extra.hint : extra.textSub,
            ),
            const SizedBox(height: 6),
            Text(
              badge.name,
              textAlign: TextAlign.center,
              style: profile.captionStyle.copyWith(
                color: badge.earned
                    ? Theme.of(context).colorScheme.onSurface
                    : extra.textSub,
                fontWeight: badge.earned ? FontWeight.bold : FontWeight.normal,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              badge.earned ? '已点亮' : '未解锁',
              style: profile.captionStyle.copyWith(
                fontSize: profile.captionStyle.fontSize != null
                    ? profile.captionStyle.fontSize! - 2
                    : 10,
                color: badge.earned ? extra.hint : extra.textSub,
              ),
            ),
          ],
        ),
      );
}
