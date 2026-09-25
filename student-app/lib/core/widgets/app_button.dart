/// 按钮 —— UI 规范 §8.1 + §4.1。
///
/// 高度自动跟分龄档位走（低年级 64dp / 中 56dp / 高 52dp），
/// **不允许页面自己写死高度** —— 否则一处分龄适配失效，小学生就点不准。
library;

import 'package:flutter/material.dart';

import '../theme/age_profile.dart';
import '../theme/app_theme.dart';

/// 按钮层级。
enum AppButtonKind {
  /// 主操作（一屏最多一个）。
  primary,

  /// 次级操作。
  secondary,

  /// 文字按钮（跳转链接，如「第一次用？去注册」）。
  text,
}

/// 符合分龄档位的按钮。
class AppButton extends StatelessWidget {
  const AppButton({
    super.key,
    required this.label,
    this.onPressed,
    this.kind = AppButtonKind.primary,
    this.icon,
    this.expand = true,
    this.loading = false,
  });

  final String label;
  final VoidCallback? onPressed;
  final AppButtonKind kind;
  final IconData? icon;

  /// 是否撑满宽度（主操作默认撑满，符合"大按钮好点"）。
  final bool expand;

  /// 提交中：显示进度圈并禁用（**防重复提交**）。
  final bool loading;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);
    final palette = Theme.of(context).colorScheme;
    final enabled = onPressed != null && !loading;

    final (double height, Color bg, Color fg) = switch (kind) {
      AppButtonKind.primary => (
          profile.safePrimaryHeight,
          enabled ? palette.primary : extra.divider,
          enabled ? palette.onPrimary : extra.textFaint,
        ),
      AppButtonKind.secondary => (
          profile.secondaryButtonHeight,
          extra.card,
          extra.textSub,
        ),
      AppButtonKind.text => (
          AgeProfile.minTouchTarget,
          Colors.transparent,
          palette.primary,
        ),
    };

    final child = Row(
      mainAxisSize: expand ? MainAxisSize.max : MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        if (loading) ...[
          SizedBox(
            width: profile.buttonText,
            height: profile.buttonText,
            child: CircularProgressIndicator(strokeWidth: 2, color: fg),
          ),
          const SizedBox(width: 10),
        ] else if (icon != null) ...[
          Icon(icon, size: profile.buttonText + 4, color: fg),
          const SizedBox(width: 8),
        ],
        Flexible(
          child: Text(
            label,
            style: profile.buttonTextStyle.copyWith(color: fg),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );

    final content = kind == AppButtonKind.text
        ? Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: child,
          )
        : SizedBox(height: height, child: Center(child: child));

    return Semantics(
      button: true,
      enabled: enabled,
      label: label,
      child: Material(
        color: bg,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: enabled ? onPressed : null,
          child: kind == AppButtonKind.secondary
              ? DecoratedBox(
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: extra.divider, width: 0.5),
                  ),
                  child: content,
                )
              : content,
        ),
      ),
    );
  }
}

/// 相邻两个按钮之间必须留 ≥20dp（UI 规范 §4.1 防误触）。
class AppButtonRow extends StatelessWidget {
  const AppButtonRow({super.key, required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < children.length; i++) ...[
          if (i > 0) const SizedBox(height: AgeProfile.adjacentButtonGap),
          children[i],
        ],
      ],
    );
  }
}
