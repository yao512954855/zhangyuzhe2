/// 卡片式单选件 —— UI 规范 P5 要点「所有选项用卡片/胶囊按钮，**绝不用下拉框**」。
///
/// 下拉框是给成人用的：小学生（尤其低年级）点开下拉、滚动、再点选，
/// 三次操作里很容易点空或选错行；而且下拉展开时会遮住上下文。
/// 卡片式一次点击到位，选项全部可见。
///
/// 选中态 = **主色浅底 + 主色描边 + 对勾**（三通道），
/// 不只靠"底色变一下"（§5.2 状态反馈 / §10 无障碍）。
///
/// 本文件归 WP-APP-02；APP-03/04/05 可 import 复用，**不要另写一份**。
library;

import 'package:flutter/material.dart';

import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';

/// 一枚可选项。
class ChoiceCard extends StatelessWidget {
  const ChoiceCard({
    super.key,
    required this.label,
    required this.selected,
    required this.onTap,
    this.icon,
    this.enabled = true,
    this.trailing,
    this.expand = false,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  /// 可选的图标（如「只练错题」配一个标记图标，帮助学生认字不多时识别）。
  final IconData? icon;

  /// 不可用（如"随机 N 个"还没选 N）——**保留可见但点不动**，
  /// 而不是直接隐藏，避免学生找不到刚才那个选项。
  final bool enabled;

  /// 右侧附加内容（如数量 N 的步进器）。
  final Widget? trailing;

  /// 是否撑满给定宽度（年级九宫格这类等宽排布用）。
  final bool expand;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;

    final fg = !enabled
        ? extra.textFaint
        : selected
            ? palette.primary
            : extra.textSub;

    return Semantics(
      button: true,
      selected: selected,
      enabled: enabled,
      label: label,
      child: Material(
        color: selected ? palette.primaryContainer : extra.card,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: enabled ? onTap : null,
          child: Container(
            constraints: BoxConstraints(minHeight: profile.secondaryButtonHeight),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: selected ? palette.primary : extra.divider,
                width: selected ? 1.5 : 0.5,
              ),
            ),
            child: Row(
              mainAxisSize: expand ? MainAxisSize.max : MainAxisSize.min,
              mainAxisAlignment: expand ? MainAxisAlignment.center : MainAxisAlignment.start,
              children: [
                // 第三通道：对勾。色盲学生只看描边也能分辨
                if (selected) ...[
                  Icon(Icons.check_rounded, size: profile.caption + 4, color: palette.primary),
                  const SizedBox(width: 6),
                ] else if (icon != null) ...[
                  Icon(icon, size: profile.caption + 4, color: fg),
                  const SizedBox(width: 6),
                ],
                Flexible(
                  child: Text(
                    label,
                    style: profile.bodyStyle.copyWith(color: fg),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (trailing != null) ...[
                  const SizedBox(width: 10),
                  trailing!,
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 一组卡片，自动换行（`Wrap` 而不是 `Row`：低年级字号大，
/// 固定行宽在窄屏上必然溢出）。
class ChoiceGroup<T> extends StatelessWidget {
  const ChoiceGroup({
    super.key,
    required this.options,
    required this.selected,
    required this.onSelect,
    this.labelOf,
    this.iconOf,
    this.trailingOf,
  });

  final List<T> options;
  final T selected;
  final ValueChanged<T> onSelect;

  final String Function(T value)? labelOf;
  final IconData? Function(T value)? iconOf;
  final Widget? Function(T value)? trailingOf;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 10,
      runSpacing: 10,
      children: [
        for (final option in options)
          ChoiceCard(
            label: labelOf?.call(option) ?? option.toString(),
            selected: option == selected,
            icon: iconOf?.call(option),
            trailing: trailingOf?.call(option),
            onTap: () => onSelect(option),
          ),
      ],
    );
  }
}

/// 配置页每个维度的小标题（"练哪部分""练多少个"…）。
class ChoiceSection extends StatelessWidget {
  const ChoiceSection({super.key, required this.title, required this.child, this.hint});

  final String title;
  final Widget child;

  /// 该维度的补充说明（口语化，别写术语 —— §9.1 文案规范）。
  final String? hint;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: profile.cardTitleStyle),
        if (hint != null) ...[
          const SizedBox(height: 4),
          Text(hint!, style: profile.captionStyle.copyWith(color: extra.textFaint)),
        ],
        const SizedBox(height: 10),
        child,
      ],
    );
  }
}
