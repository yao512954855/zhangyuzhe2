/// 设置页 —— WP-APP-05 F4（主题 / 护眼 / 服务器地址）/ F6（自动暖色）/ F8（关于）。
///
/// - 主题切换：直接复用 APP-01 的 `showThemeSwitcher`（含四态 + 自动暖色开关，
///   UI 规范 §2.4「入口放设置页第一屏」），**不另造一套**（红线：不改 core/theme）。
/// - 护眼开关：持久化到 `LocalStore.eyeCareEnabled` 并同步 `EyeCareService`；
///   关闭后累计清零，不会弹休息页（U6）。
/// - 自动暖色：复用 `ThemeController.setAutoWarm`（F6，可关）。
/// - 服务器地址：展示当前地址 + 「更换」跳发现页（U9），切完需重新登录；
///   **退出登录保留服务器地址**由 `AuthController.logout()` 保证。
/// - 关于：版本号 / 服务器地址 / 连接状态（F8）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
// ignore: depend_on_referenced_packages
import 'package:package_info_plus/package_info_plus.dart';

import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../features/auth/auth_controller.dart';
import '../../core/theme/theme_controller.dart';
import '../../features/theme/theme_switcher_sheet.dart';
import '../../router/routes/auth.dart';
import '../../router/routes/discovery.dart';

/// App 版本号（F8）。
final appVersionProvider = FutureProvider<String>((ref) async {
  try {
    final info = await PackageInfo.fromPlatform();
    final build = info.buildNumber.isEmpty ? '' : '+${info.buildNumber}';
    return '${info.version}$build';
  } on Object {
    return '1.0.0';
  }
});

/// 设置页（F4 / F6）。
class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final theme = ref.watch(themeControllerProvider);
    final eye = ref.watch(eyeCareProvider);
    final server = ref.watch(serverStoreProvider);

    return Scaffold(
      appBar: AppBar(title: Text('设置', style: profile.pageTitleStyle)),
      body: ListView(
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
        children: [
          _SectionTitle(title: '外观', profile: profile, extra: extra),
          _Row(
            profile: profile,
            extra: extra,
            icon: Icons.palette_outlined,
            title: '主题',
            subtitle: theme.mode.label,
            onTap: () => showThemeSwitcher(context),
          ),
          const SizedBox(height: 12),
          _SwitchRow(
            profile: profile,
            extra: extra,
            icon: Icons.nightlight_round,
            title: '晚上自动变暗',
            subtitle: '晚上 7 点到早上 7 点自动用夜间颜色',
            value: theme.autoWarm,
            onChanged: (v) => ref.read(themeControllerProvider.notifier).setAutoWarm(v),
          ),
          const SizedBox(height: AgeProfile.elementGap),
          _SectionTitle(title: '护眼', profile: profile, extra: extra),
          _SwitchRow(
            profile: profile,
            extra: extra,
            icon: Icons.visibility_outlined,
            title: '护眼提醒',
            subtitle: '每 20 分钟提醒休息 20 秒（间隔由管理员设置）',
            value: eye.enabled,
            onChanged: (v) => _setEyeCare(ref, v),
          ),
          const SizedBox(height: AgeProfile.elementGap),
          _SectionTitle(title: '服务器', profile: profile, extra: extra),
          _Row(
            profile: profile,
            extra: extra,
            icon: Icons.dns_outlined,
            title: '服务器地址',
            subtitle: server.current?.authority ?? '未设置',
            // ⚠️ 必须传 arguments: false —— 不传的话发现页按默认 autoFind:true
            // 启动自动查找，当前地址探活成功会立刻 pop 回本页，
            // 学生永远来不及点「扫码 / 手动输入」（真机踩坑，勿删）。
            onTap: () => _changeServer(context),
          ),
          const SizedBox(height: 12),
          _Row(
            profile: profile,
            extra: extra,
            icon: Icons.info_outline_rounded,
            title: '关于',
            subtitle: '版本号与连接状态',
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const AboutPage()),
            ),
          ),
          const SizedBox(height: AgeProfile.elementGap),
          AppButton(
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
              // 保留服务器地址由 logout() 保证；跳登录页。
              Navigator.of(context).pushNamedAndRemoveUntil(
                AuthRoutes.login,
                (route) => false,
              );
            },
          ),
        ],
      ),
    );
  }

  void _setEyeCare(WidgetRef ref, bool value) {
    ref.read(localStoreProvider).setEyeCareEnabled(value);
    ref.read(eyeCareProvider).setEnabled(value);
  }

  /// 打开服务器发现页（autoFind=false，让学生自己选扫码/手输）。
  ///
  /// ⚠️ 连上之后**由发现页统一接管**（它会清栈并落到登录页/首页），本页
  /// 不处理返回值 —— 发现页用的是 `pushNamedAndRemoveUntil`，本页会被一起
  /// 移除，所以这个 `await` 只会拿到 `null`。
  /// （2026-09-29 修「换 IP 后连接黑屏」时改的：旧写法是发现页 `pop(true)` +
  /// 本页再跳登录页，在**启动流程**下导航栈会被弹空，学生看到的就是黑屏。）
  Future<void> _changeServer(BuildContext context) async {
    await Navigator.of(context).pushNamed<void>(
      DiscoveryRoutes.setup,
      arguments: false,
    );
  }
}

/// 关于页（F8）。
class AboutPage extends ConsumerWidget {
  const AboutPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final version = ref.watch(appVersionProvider);
    final server = ref.watch(serverStoreProvider);

    return Scaffold(
      appBar: AppBar(title: Text('关于', style: profile.pageTitleStyle)),
      body: ListView(
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
        children: [
          _InfoTile(
            profile: profile,
            extra: extra,
            title: '版本号',
            value: version.when(
              data: (v) => v,
              loading: () => '…',
              error: (_, _) => '未知',
            ),
          ),
          const SizedBox(height: 12),
          _InfoTile(
            profile: profile,
            extra: extra,
            title: '服务器地址',
            value: server.current?.authority ?? '未设置',
          ),
          const SizedBox(height: 12),
          const _ConnectionTile(),
        ],
      ),
    );
  }
}

/// 连接状态（F8）：真实 ping 后端。
class _ConnectionTile extends ConsumerWidget {
  const _ConnectionTile();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final connected = ref.watch(_connectivityProvider);

    final ok = connected.asData?.value ?? false;
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
        child: Row(
          children: [
            Icon(
              ok ? Icons.cloud_done_outlined : Icons.cloud_off_outlined,
              color: ok ? extra.success : extra.error,
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('连接状态', style: profile.bodyStyle),
                  Text(
                    connected.isLoading
                        ? '检测中…'
                        : (ok ? '已连接到服务器' : '暂时连不上服务器'),
                    style: profile.captionStyle.copyWith(color: extra.textSub),
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

final _connectivityProvider = FutureProvider<bool>((ref) async {
  final info = await ref.watch(apiClientProvider).ping();
  return info != null;
});

// ---------------------------------------------------------------------------
// 小组件
// ---------------------------------------------------------------------------

class _SectionTitle extends StatelessWidget {
  const _SectionTitle({
    required this.profile,
    required this.extra,
    required this.title,
  });

  final AgeProfile profile;
  final AppExtraColors extra;
  final String title;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(left: 4, bottom: 8),
        child: Text(
          title,
          style: profile.captionStyle.copyWith(color: extra.textFaint),
        ),
      );
}

class _Row extends StatelessWidget {
  const _Row({
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

class _SwitchRow extends StatelessWidget {
  const _SwitchRow({
    required this.profile,
    required this.extra,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
  });

  final AgeProfile profile;
  final AppExtraColors extra;
  final IconData icon;
  final String title;
  final String subtitle;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
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
              Switch(value: value, onChanged: onChanged),
            ],
          ),
        ),
      );
}

class _InfoTile extends StatelessWidget {
  const _InfoTile({
    required this.profile,
    required this.extra,
    required this.title,
    required this.value,
  });

  final AgeProfile profile;
  final AppExtraColors extra;
  final String title;
  final String value;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: profile.captionStyle.copyWith(color: extra.textSub)),
                    const SizedBox(height: 4),
                    Text(value, style: profile.bodyStyle),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
}
