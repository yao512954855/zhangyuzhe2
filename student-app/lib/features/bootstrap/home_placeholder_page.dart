/// 临时落地页 —— **只为让 APP-01 自己可验收，APP-02 落地后自动失效**。
///
/// 为什么需要它：APP-01 的验收用例 U7（杀进程重开免登录直达首页）
/// 依赖一个"登录后能落地的页面"，而真正的首页 P4 属于 WP-APP-02。
///
/// 衔接方式（**APP-02 不需要改本文件，也不需要改聚合器**）：
/// `AppRouter.homeRoute` 会检查 `lib/router/routes/home.dart` 里
/// 是否注册了 `/home`；一旦 APP-02 注册了，跳转目标自动切到真首页，
/// 这个页面就再也不会被访问到。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../auth/auth_controller.dart';
import '../theme/theme_switcher_sheet.dart';

/// 临时落地页。
class HomePlaceholderPage extends ConsumerWidget {
  const HomePlaceholderPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final auth = ref.watch(authControllerProvider);
    final grade = ref.watch(gradePrefProvider);
    final server = ref.watch(serverStoreProvider).current;

    return Scaffold(
      appBar: AppBar(
        title: const Text('背记系统'),
        actions: [
          IconButton(
            tooltip: '换个颜色',
            icon: const Icon(Icons.palette_outlined),
            onPressed: () => showThemeSwitcher(context),
          ),
        ],
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AgeProfile.screenEdgeInset * 1.5),
          children: [
            Text(
              '你好，${auth.user?.displayName ?? '同学'}',
              style: profile.pageTitleStyle,
            ),
            const SizedBox(height: 6),
            Text(
              grade.isSet ? grade.label : '还没选年级学期',
              style: profile.captionStyle.copyWith(color: extra.textSub),
            ),
            const SizedBox(height: 24),
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: extra.card,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: extra.divider, width: 0.5),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(Icons.check_circle_rounded, color: extra.success, size: 22),
                      const SizedBox(width: 8),
                      Text('已经连上服务器了', style: profile.bodyStyle),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(
                    server == null ? '地址未知' : server.display,
                    style: profile.captionStyle.copyWith(color: extra.textFaint),
                  ),
                  if (auth.offline) ...[
                    const SizedBox(height: 12),
                    const InlineNotice(
                      text: '现在连不上服务器，先用上次的信息进来了',
                      kind: NoticeKind.hint,
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 24),
            const InlineNotice(
              text: '这是 APP-01 的临时页面，真正的首页由 APP-02 提供',
              kind: NoticeKind.info,
            ),
            const SizedBox(height: 24),
            AppButton(
              label: '换个颜色',
              kind: AppButtonKind.secondary,
              icon: Icons.palette_outlined,
              onPressed: () => showThemeSwitcher(context),
            ),
            const SizedBox(height: AgeProfile.adjacentButtonGap),
            AppButton(
              label: '退出登录',
              kind: AppButtonKind.secondary,
              onPressed: () async {
                await ref.read(authControllerProvider.notifier).logout();
                if (context.mounted) {
                  Navigator.of(context).pushReplacementNamed('/login');
                }
              },
            ),
          ],
        ),
      ),
    );
  }
}
