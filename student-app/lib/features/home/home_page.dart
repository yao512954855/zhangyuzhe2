/// 学生端首页 —— 简化版 WP-C（30-学生端工作包规格 功能点 1）。
///
/// 只剩两个大按钮：**听背**（进单元列表选内容）与**错题本**。
/// 错题本页面由 WP-E（批次4）交付，在此之前按钮置灰 + 「即将开放」提示
/// （跨包接力项 J2：E 落地后由集成阶段接线，见 00-总纲 §4）。
library;

import 'package:flutter/material.dart';

import '../../router/routes/home.dart';
import '../../router/routes/wrongbook.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../theme/theme_switcher_sheet.dart';

/// 首页。
class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('背记系统'),
        centerTitle: true,
        // 主题切换入口（UI 规范 §2.4「第一屏、不埋二级菜单」）：
        // 弹层组件本体在 features/theme/theme_switcher_sheet.dart（APP-01 交付），
        // 简化版砍掉旧「我的」体系后这里成了唯一的第一屏，入口挂回 AppBar。
        actions: [
          IconButton(
            tooltip: '主题',
            icon: const Icon(Icons.palette_outlined),
            onPressed: () => showThemeSwitcher(context),
          ),
        ],
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(AgeProfile.screenEdgeInset * 2),
          child: Column(
            children: [
              const Spacer(flex: 2),
              _HomeEntry(
                icon: Icons.edit_note_rounded,
                label: '听背',
                hint: '选单元，听写或背诵',
                onTap: () =>
                    Navigator.of(context).pushNamed(HomeRoutes.unitList),
              ),
              const SizedBox(height: AgeProfile.adjacentButtonGap * 1.5),
              _HomeEntry(
                icon: Icons.auto_stories_rounded,
                label: '错题本',
                hint: '错了的题都在这',
                // J2 销账：WP-E 已交付（2026-10-09 集成接线）。
                enabled: true,
                onTap: () =>
                    Navigator.of(context).pushNamed(WrongbookRoutes.wrongbook),
              ),
              const Spacer(flex: 3),
              Text(
                '先把内容录进后台，再从这里开始练',
                style: profile.captionStyle.copyWith(color: extra.textFaint),
              ),
              const SizedBox(height: AgeProfile.screenEdgeInset),
            ],
          ),
        ),
      ),
    );
  }
}

/// 首页入口大按钮（触控高度跟分龄档位走，≥48dp 硬下限）。
class _HomeEntry extends StatelessWidget {
  const _HomeEntry({
    required this.icon,
    required this.label,
    required this.hint,
    this.enabled = true,
    this.onTap,
  });

  final IconData icon;
  final String label;
  final String hint;
  final bool enabled;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;

    final bg = enabled ? palette.primary : extra.card;
    final fg = enabled ? palette.onPrimary : extra.textFaint;

    return Semantics(
      button: true,
      enabled: enabled,
      label: label,
      child: Material(
        color: bg,
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: enabled ? onTap : null,
          child: SizedBox(
            height: profile.primaryButtonHeight * 2,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(icon, size: 40, color: fg),
                const SizedBox(width: 16),
                Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(label, style: profile.pageTitleStyle.copyWith(color: fg)),
                    Text(
                      hint,
                      style: profile.captionStyle.copyWith(
                        color: enabled
                            ? fg.withValues(alpha: 0.8)
                            : extra.textFaint,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
