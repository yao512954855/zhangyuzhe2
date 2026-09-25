/// 主题切换弹层 —— UI 规范 §2.4「切换入口放在设置页第一屏，不埋在二级菜单」。
///
/// **为什么不直接做「设置页」**：P12 设置页属于 WP-APP-05，
/// APP-01 若自己建一个会撞车。所以这里只提供**组件**，
/// APP-01 的临时落地页先挂上（好让 U9 能验），APP-05 的设置页直接复用同一个组件。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/theme/theme_controller.dart';

/// 弹出主题选择面板。
Future<void> showThemeSwitcher(BuildContext context) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: AppExtraColors.of(context).card,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
    ),
    builder: (_) => const ThemeSwitcherSheet(),
  );
}

/// 主题选择面板。
class ThemeSwitcherSheet extends ConsumerWidget {
  const ThemeSwitcherSheet({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(themeControllerProvider);
    final controller = ref.read(themeControllerProvider.notifier);
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('选个看着舒服的', style: profile.cardTitleStyle),
            const SizedBox(height: 4),
            Text(
              '觉得刺眼就换个颜色，晚上会自动变暗',
              style: profile.captionStyle.copyWith(color: extra.textSub),
            ),
            const SizedBox(height: AgeProfile.elementGap),
            for (final mode in AppThemeMode.values) ...[
              _ModeTile(
                mode: mode,
                selected: state.mode == mode,
                onTap: () => controller.setMode(mode),
              ),
              const SizedBox(height: 8),
            ],
            const SizedBox(height: 4),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: extra.card,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: extra.divider, width: 0.5),
              ),
              child: Row(
                children: [
                  Icon(Icons.nightlight_round, color: extra.hint, size: 22),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text('晚上自动变暗', style: profile.bodyStyle),
                        Text(
                          '晚上 7 点到早上 7 点自动用夜间颜色',
                          style: profile.captionStyle.copyWith(color: extra.textFaint),
                        ),
                      ],
                    ),
                  ),
                  Switch(
                    value: state.autoWarm,
                    onChanged: (v) => controller.setAutoWarm(v),
                  ),
                ],
              ),
            ),
            const SizedBox(height: AgeProfile.elementGap),
            Text(
              '选好之后每次打开都会用它',
              textAlign: TextAlign.center,
              style: profile.captionStyle.copyWith(color: extra.textFaint),
            ),
          ],
        ),
      ),
    );
  }
}

class _ModeTile extends StatelessWidget {
  const _ModeTile({required this.mode, required this.selected, required this.onTap});

  final AppThemeMode mode;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);
    final primary = Theme.of(context).colorScheme.primary;

    final preview = switch (mode) {
      AppThemeMode.system => const [Color(0xFFF7F5F0), Color(0xFF1B1F23)],
      AppThemeMode.day => const [Color(0xFFF7F5F0), Color(0xFFFFFFFF)],
      AppThemeMode.paper => const [Color(0xFFF5EEDF), Color(0xFFFBF7ED)],
      AppThemeMode.night => const [Color(0xFF1B1F23), Color(0xFF24292E)],
    };

    return SizedBox(
      height: profile.listItemHeight,
      child: Material(
        color: selected ? Theme.of(context).colorScheme.primaryContainer : extra.card,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14),
            child: Row(
              children: [
                // 色块预览：比只看文字名更直观
                for (final color in preview)
                  Container(
                    width: 22,
                    height: 22,
                    margin: const EdgeInsets.only(right: 4),
                    decoration: BoxDecoration(
                      color: color,
                      shape: BoxShape.circle,
                      border: Border.all(color: extra.divider, width: 0.5),
                    ),
                  ),
                const SizedBox(width: 10),
                Expanded(child: Text(mode.label, style: profile.bodyStyle)),
                if (selected)
                  Icon(Icons.check_circle_rounded, color: primary, size: 22),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
