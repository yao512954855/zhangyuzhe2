/// 首页顶部：学生姓名 + 当前年级学期 + 进度概要 —— UI 规范 P4 / F1 / F2。
///
/// 只接收回调、不自己跳路由 —— 「设置按钮去哪」由页面决定
/// （P12 设置页归 APP-05，还没落地时按钮要能退化，见 `home_page.dart`）。
library;

import 'package:flutter/material.dart';

import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/feedback.dart';
import 'models.dart';
import 'time_format.dart';

class ProgressHeader extends StatelessWidget {
  const ProgressHeader({
    super.key,
    required this.displayName,
    required this.preference,
    required this.onTapGrade,
    required this.onTapSettings,
    this.subject = Subject.english,
    this.progress,
    this.preferencePending = false,
    this.offline = false,
  });

  final String displayName;

  /// 当前年级学期（本地偏好）。
  final GradeSemester preference;

  /// 当前学科 —— 概要数字要跟着切（F4）。
  ///
  /// `/api/me/progress` 一次给了词条与课文两套数（`*_words` / `*_texts`），
  /// 但**学生看到的应该是当前 Tab 的那一套**：语文 Tab 里显示
  /// 「已练词 0 / 已掌握 0」既看不懂、也不是他关心的。
  final Subject subject;

  final VoidCallback onTapGrade;
  final VoidCallback onTapSettings;

  final ProgressOverview? progress;

  /// 已存本机、还没同步到服务端（断网时选过）。
  final bool preferencePending;

  /// 数据来自缓存。
  final bool offline;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                '你好，$displayName',
                style: profile.pageTitleStyle,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            // 触控目标受 48dp 下限保护（§4.1）
            SizedBox(
              width: profile.safeIconSize,
              height: profile.safeIconSize,
              child: IconButton(
                tooltip: '设置',
                icon: Icon(Icons.settings_outlined, size: profile.safeIconSize * 0.5),
                onPressed: onTapSettings,
              ),
            ),
          ],
        ),
        const SizedBox(height: 4),
        _GradeChip(
          label: gradeSemesterLabel(preference.grade, preference.semester),
          selected: preference.isSet,
          onTap: onTapGrade,
        ),
        if (progress != null) ...[
          const SizedBox(height: AgeProfile.elementGap),
          _SummaryRow(progress: progress!, subject: subject),
        ],
        if (preferencePending) ...[
          const SizedBox(height: 12),
          const InlineNotice(
            text: '年级已存在这台手机上，连上网会自动同步',
            kind: NoticeKind.hint,
          ),
        ],
        if (offline) ...[
          const SizedBox(height: 12),
          const InlineNotice(
            text: '当前无法连接服务器，下面显示的是上次的数据',
            kind: NoticeKind.error,
          ),
        ],
        const SizedBox(height: 4),
        Divider(color: extra.divider, height: 1),
      ],
    );
  }
}

/// 年级学期胶囊 —— **可点即切**（P4 的 `三年级 · 上学期 ▾`）。
class _GradeChip extends StatelessWidget {
  const _GradeChip({required this.label, required this.selected, required this.onTap});

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;

    return Semantics(
      button: true,
      label: '当前 $label，点一下可以切换',
      child: Material(
        color: selected ? palette.primaryContainer : extra.card,
        borderRadius: BorderRadius.circular(10),
        child: InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  label,
                  style: profile.bodyStyle.copyWith(
                    color: selected ? palette.onPrimaryContainer : extra.textSub,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const SizedBox(width: 6),
                Icon(
                  Icons.expand_more_rounded,
                  size: profile.caption + 6,
                  color: selected ? palette.onPrimaryContainer : extra.textSub,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 进度概要：已练 / 已掌握 / 连续天数（F1）—— **随学科切换**（F4）。
class _SummaryRow extends StatelessWidget {
  const _SummaryRow({required this.progress, required this.subject});

  final ProgressOverview progress;
  final Subject subject;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);

    // 两套数据都来自 `/api/me/progress`（一次请求拿全，切 Tab 不再请求）。
    final items = <({IconData icon, Color color, String value, String label})>[
      if (subject == Subject.english) ...[
        (
          icon: Icons.edit_note_rounded,
          color: extra.textSub,
          value: '${progress.practicedWords}',
          label: '已练词',
        ),
        (
          icon: Icons.star_rounded,
          color: extra.hint,
          value: '${progress.masteredWords}',
          label: '已掌握',
        ),
      ] else
        // 语文没有「掌握」这一档（后端只有背诵次数/评级），
        // 与其硬凑一个数字，不如只报"背了几篇"。
        (
          icon: Icons.menu_book_rounded,
          color: extra.textSub,
          value: '${progress.recitedTexts}/${progress.totalTexts}',
          label: '已背篇',
        ),
      (
        icon: Icons.local_fire_department_rounded,
        color: extra.hint,
        value: '${progress.streakDays}',
        label: '连续天数',
      ),
    ];

    return Wrap(
      spacing: 18,
      runSpacing: 8,
      children: [
        for (final item in items)
          Semantics(
            label: '${item.label} ${item.value}',
            excludeSemantics: true,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(item.icon, size: profile.caption + 6, color: item.color),
                const SizedBox(width: 4),
                Text(
                  item.value,
                  style: profile.cardTitleStyle.copyWith(color: item.color),
                ),
                const SizedBox(width: 4),
                Text(item.label, style: profile.captionStyle.copyWith(color: extra.textSub)),
              ],
            ),
          ),
      ],
    );
  }
}
