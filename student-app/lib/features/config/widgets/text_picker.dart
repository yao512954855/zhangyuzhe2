/// 语文「选课文」—— F8 的范围选项之一。
///
/// 这里的"段"落在**课文**这一层：后端 `_create_chinese_session` 按
/// `scope_detail.text_ids` 筛课文，没有"单元 → 课 → 段"的中间层级
/// （units 就是最小组织单位）。所以选项文案写「选课文」，
/// **不写「选段」** —— 让学生选一个实际不生效的粒度是欺骗。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/age_profile.dart';
import '../../../core/theme/app_theme.dart';
import '../../../core/util/format.dart';
import '../../home/models.dart';
import '../practice_estimate.dart';

class TextPicker extends StatelessWidget {
  const TextPicker({
    super.key,
    required this.texts,
    required this.selectedIds,
    required this.onToggle,
    this.onSelectAll,
    this.now,
  });

  final List<TextBrief> texts;
  final Set<int> selectedIds;
  final ValueChanged<int> onToggle;

  /// 「全选 / 全不选」—— 一个单元十几篇时，逐个点太累。
  final ValueChanged<bool>? onSelectAll;

  final DateTime? now;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final allSelected = texts.isNotEmpty && selectedIds.length == texts.length;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (onSelectAll != null && texts.length > 1)
          Align(
            alignment: Alignment.centerRight,
            child: TextButton(
              onPressed: () => onSelectAll!(!allSelected),
              child: Text(allSelected ? '都不选' : '全选'),
            ),
          ),
        for (final text in texts) ...[
          _TextRow(
            text: text,
            selected: selectedIds.contains(text.id),
            onTap: () => onToggle(text.id),
          ),
          const SizedBox(height: 8),
        ],
        if (texts.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 12),
            child: Text(
              '这个单元还没有课文',
              style: profile.captionStyle.copyWith(color: extra.textFaint),
            ),
          ),
      ],
    );
  }
}

class _TextRow extends StatelessWidget {
  const _TextRow({required this.text, required this.selected, required this.onTap});

  final TextBrief text;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;

    final subtitle = [
      if (text.author != null && text.author!.isNotEmpty) text.author!,
      if (text.reciteScopeChars > 0) '背诵 ${text.reciteScopeChars} 字',
      formatReciteSeconds(reciteSecondsFor(text)),
      if (text.hasRecited) '上次 ${text.lastGrade}',
    ].join(' · ');

    return Semantics(
      checked: selected,
      label: '${text.title}，$subtitle',
      excludeSemantics: true,
      child: Material(
        color: selected ? palette.primaryContainer : extra.card,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: onTap,
          child: Container(
            constraints: BoxConstraints(minHeight: profile.listItemHeight),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: selected ? palette.primary : extra.divider,
                width: selected ? 1.5 : 0.5,
              ),
            ),
            child: Row(
              children: [
                // 复选框 + 文字的**双通道**：不靠底色单独表意（§10 无障碍）
                Icon(
                  selected
                      ? Icons.check_box_rounded
                      : Icons.check_box_outline_blank_rounded,
                  size: profile.caption + 10,
                  color: selected ? palette.primary : extra.textFaint,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        text.title,
                        style: profile.bodyStyle.copyWith(
                          fontWeight: FontWeight.w500,
                          color: selected ? palette.primary : null,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 2),
                      Text(
                        subtitle,
                        style: profile.captionStyle.copyWith(color: extra.textSub),
                        maxLines: 2,
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 秒数 → 「约 6 分钟」（复用 APP-01 的格式化口径，不另造一套）。
String formatReciteSeconds(int seconds) => formatEstimate(seconds);
