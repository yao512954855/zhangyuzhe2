/// 「我的」总入口 —— WP-APP-05（F1~F9 的统一落地页）。
///
/// 主壳把 `/mistakes`（「错题本」底部 Tab）映射到本页，因此本页是「我的」
/// 所有功能的入口：错题本、我的记录、我的统计、设置、关于、退出登录。
///
/// 路由约定（红线 1，由 `mistakes.dart` 落位）：
/// - `/mistakes`        → 本页
/// - `/mistakes/records` → MyRecordsPage（「我的」底部 Tab）
/// - `/mistakes/settings` → SettingsPage
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../features/auth/auth_controller.dart';
import '../../router/routes/auth.dart';
import 'my_records_page.dart' show MyRecordsPage, mineProgressProvider;
import 'my_stats_page.dart' show MyStatsPage;
import 'settings_page.dart' show AboutPage, SettingsPage;
import 'wrong_book_page.dart' show WrongBookPage;

/// 「我的」首页（错题本 Tab 内容）。
class MinePage extends ConsumerWidget {
  const MinePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final user = ref.watch(authControllerProvider).user;
    final progress = ref.watch(mineProgressProvider);

    return Scaffold(
      appBar: AppBar(title: Text('我的', style: profile.pageTitleStyle)),
      body: ListView(
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
        children: [
          _Header(
            name: user?.displayName ?? '同学',
            subtitle: progress.when(
              data: (p) => '连续练习 ${p.streakDays} 天 · 已掌握 ${p.masteredWords} 个词',
              loading: () => '正在加载…',
              error: (_, _) => '练习数据暂时打不开',
            ),
            profile: profile,
            extra: extra,
          ),
          const SizedBox(height: AgeProfile.elementGap),
          _Entry(
            profile: profile,
            extra: extra,
            icon: Icons.error_outline_rounded,
            title: '错题本',
            subtitle: '把练错的词再练一遍',
            onTap: () => _push(context, const WrongBookPage()),
          ),
          const SizedBox(height: 12),
          _Entry(
            profile: profile,
            extra: extra,
            icon: Icons.history_rounded,
            title: '我的记录',
            subtitle: '今日练习与连续天数',
            onTap: () => _push(context, const MyRecordsPage()),
          ),
          const SizedBox(height: 12),
          _Entry(
            profile: profile,
            extra: extra,
            icon: Icons.insights_outlined,
            title: '我的统计',
            subtitle: '正确率与进步情况',
            onTap: () => _push(context, const MyStatsPage()),
          ),
          const SizedBox(height: 12),
          _Entry(
            profile: profile,
            extra: extra,
            icon: Icons.settings_outlined,
            title: '设置',
            subtitle: '主题 · 护眼 · 服务器地址',
            onTap: () => _push(context, const SettingsPage()),
          ),
          const SizedBox(height: 12),
          _Entry(
            profile: profile,
            extra: extra,
            icon: Icons.info_outline_rounded,
            title: '关于',
            subtitle: '版本号与连接状态',
            onTap: () => _push(context, const AboutPage()),
          ),
          const SizedBox(height: AgeProfile.elementGap),
          _LogoutButton(profile: profile, extra: extra),
        ],
      ),
    );
  }

  void _push(BuildContext context, Widget page) {
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => page));
  }
}

class _Header extends StatelessWidget {
  const _Header({
    required this.name,
    required this.subtitle,
    required this.profile,
    required this.extra,
  });

  final String name;
  final String subtitle;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Row(
            children: [
              CircleAvatar(
                radius: 26,
                backgroundColor: extra.hintLight,
                child: Icon(Icons.person_outline_rounded, color: extra.hint, size: 28),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('你好，$name', style: profile.cardTitleStyle),
                    const SizedBox(height: 4),
                    Text(subtitle, style: profile.captionStyle.copyWith(color: extra.textSub)),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
}

class _Entry extends StatelessWidget {
  const _Entry({
    required this.profile,
    required this.extra,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
  });

  final AgeProfile profile;
  final AppExtraColors extra;
  final IconData icon;
  final String title;
  final String subtitle;
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

class _LogoutButton extends ConsumerWidget {
  const _LogoutButton({required this.profile, required this.extra});

  final AgeProfile profile;
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
        Navigator.of(context).pushNamedAndRemoveUntil(
          AuthRoutes.login,
          (route) => false,
        );
      },
    );
  }
}
