/// 我的记录 / 「我的」底部 Tab —— WP-APP-05 F2（U3）。
///
/// 数据来自 `GET /api/me/progress`（契约 §8.4）。**契约没有**「练习场次列表 /
/// 单次详情」接口（`sessions.py` 只有 3 个 POST，无 GET 列表/详情），
/// 所以这里**只渲染真能拿到的汇总**：今日练习、连续天数、已掌握词条、
/// 正确率、累计练习量——**绝不伪造场次列表或单次详情**（红线 4）。
///
/// 该文件同时导出 [mineProgressProvider]，供「我的」首页与统计页复用。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../auth/auth_controller.dart';
import '../home/models.dart';
import 'settings_page.dart' show AboutPage, SettingsPage;
import 'my_stats_page.dart' show MyStatsPage;

/// 进度总览（我的记录 / 我的统计 / 首页共用）。
///
/// 走生产代码 `ApiClient`，由页面 watch；失败给中文错误，不静默。
final mineProgressProvider = FutureProvider<ProgressOverview>((ref) async {
  final data = await ref.watch(apiClientProvider).get<Map<String, dynamic>>(
        '/api/me/progress',
      );
  return ProgressOverview.fromJson(data ?? const <String, dynamic>{});
});

/// 「我的」底部 Tab（F2 主体 + 其余功能的入口）。
class MyRecordsPage extends ConsumerWidget {
  const MyRecordsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final progress = ref.watch(mineProgressProvider);
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(title: Text('我的', style: profile.pageTitleStyle)),
      body: _Body(progress: progress, profile: profile, extra: extra),
    );
  }
}

class _Body extends ConsumerWidget {
  const _Body({
    required this.progress,
    required this.profile,
    required this.extra,
  });

  final AsyncValue<ProgressOverview> progress;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return progress.when(
      loading: () => const LoadingView(),
      error: (e, _) => ErrorView(
        message: e is ApiException ? e.displayText : '暂时拿不到练习记录',
        onRetry: () => ref.invalidate(mineProgressProvider),
      ),
      data: (p) => ListView(
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
        children: [
          _SummaryGrid(progress: p, profile: profile, extra: extra),
          const SizedBox(height: AgeProfile.elementGap),
          _EntryTile(
            icon: Icons.insights_outlined,
            title: '我的统计',
            subtitle: '正确率与进步情况',
            profile: profile,
            extra: extra,
            onTap: () => _push(context, const MyStatsPage()),
          ),
          const SizedBox(height: 12),
          _EntryTile(
            icon: Icons.settings_outlined,
            title: '设置',
            subtitle: '主题 · 护眼 · 服务器地址',
            profile: profile,
            extra: extra,
            onTap: () => _push(context, const SettingsPage()),
          ),
          const SizedBox(height: 12),
          _EntryTile(
            icon: Icons.info_outline_rounded,
            title: '关于',
            subtitle: '版本号与连接状态',
            profile: profile,
            extra: extra,
            onTap: () => _push(context, const AboutPage()),
          ),
          const SizedBox(height: AgeProfile.elementGap),
          _LogoutButton(extra: extra),
        ],
      ),
    );
  }

  void _push(BuildContext context, Widget page) {
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => page));
  }
}

/// F2 真实汇总卡：今日练习 / 连续天数 / 已掌握 / 正确率 / 累计。
class _SummaryGrid extends StatelessWidget {
  const _SummaryGrid({
    required this.progress,
    required this.profile,
    required this.extra,
  });

  final ProgressOverview progress;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) {
    final today = progress.today;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('今日练习', style: profile.cardTitleStyle),
            const SizedBox(height: 12),
            Row(
              children: [
                _Stat(value: '${today.sessions}', label: '练习场次', extra: extra, profile: profile),
                _Stat(value: '${today.items}', label: '练习词条', extra: extra, profile: profile),
                _Stat(value: '${today.correct}', label: '答对', extra: extra, profile: profile),
              ],
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                _Stat(value: '${progress.streakDays}', label: '连续练习(天)', extra: extra, profile: profile),
                _Stat(value: '${progress.masteredWords}', label: '已掌握词条', extra: extra, profile: profile),
                _Stat(
                  value: '${(progress.accuracy * 100).round()}%',
                  label: '正确率',
                  extra: extra,
                  profile: profile,
                ),
              ],
            ),
            const SizedBox(height: 16),
            Text(
              '累计练习 ${progress.practicedWords}/${progress.totalWords} 个英语词条',
              style: profile.captionStyle.copyWith(color: extra.textSub),
            ),
          ],
        ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({
    required this.value,
    required this.label,
    required this.extra,
    required this.profile,
  });

  final String value;
  final String label;
  final AppExtraColors extra;
  final AgeProfile profile;

  @override
  Widget build(BuildContext context) => Expanded(
        child: Column(
          children: [
            Text(
              value,
              style: profile.wordStyle.copyWith(color: extra.hint),
            ),
            const SizedBox(height: 4),
            Text(label, style: profile.captionStyle.copyWith(color: extra.textSub)),
          ],
        ),
      );
}

/// 通用入口行（复用「我的」首页同款样式）。
class _EntryTile extends StatelessWidget {
  const _EntryTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.profile,
    required this.extra,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final AgeProfile profile;
  final AppExtraColors extra;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Card(
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(12),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
            child: Row(
              children: [
                Icon(icon, color: extra.hint, size: 24),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title, style: profile.bodyStyle),
                      Text(subtitle, style: profile.captionStyle.copyWith(color: extra.textSub)),
                    ],
                  ),
                ),
                Icon(Icons.chevron_right_rounded, color: extra.textFaint),
              ],
            ),
          ),
        ),
      );
}

/// 退出登录（F7）：清登录态、保留服务器地址、回登录页。
class _LogoutButton extends ConsumerWidget {
  const _LogoutButton({required this.extra});

  final AppExtraColors extra;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return AppButton(
      label: '退出登录',
      kind: AppButtonKind.secondary,
      onPressed: () async {
        final sure = await showDialog<bool>(
              context: context,
              builder: (dialogContext) => AlertDialog(
                title: const Text('退出登录'),
                content: const Text('退出后需要重新登录，服务器地址会保留。'),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.of(dialogContext).pop(false),
                    child: const Text('取消'),
                  ),
                  TextButton(
                    onPressed: () => Navigator.of(dialogContext).pop(true),
                    child: const Text('退出'),
                  ),
                ],
              ),
            ) ??
            false;
        if (!sure || !context.mounted) {
          return;
        }
        await ref.read(authControllerProvider.notifier).logout();
        if (!context.mounted) {
          return;
        }
        // AuthRoutes.login = '/login'；保留服务器地址由 `logout()` 保证（只清 auth）。
        Navigator.of(context).pushNamedAndRemoveUntil(
          '/login',
          (route) => false,
        );
      },
    );
  }
}
