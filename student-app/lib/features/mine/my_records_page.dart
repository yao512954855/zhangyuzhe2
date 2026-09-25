/// 我的记录 / 「我的」底部 Tab —— WP-APP-05 F2（U3）。
///
/// 两块数据：
/// - **汇总**：`GET /api/me/progress`（契约 §8.4）—— 今日练习、连续天数、
///   已掌握词条、正确率、累计练习量；
/// - **场次列表**：`GET /api/sessions`（**契约 §14.1 缺口 7** → 接口设计 §9.5，
///   2026-09-25 补齐）。此前契约只有 3 个 `POST`、没有读取侧，本页只能渲染 5 个
///   汇总数字（§八 #75），与需求 §5.9「按日期列出练习场次与成绩」名不符实。
///
/// 两句「不给假选项」：**进行中的场次不可点**（服务端对 `ongoing` 拒绝下发
/// 逐题明细 —— `expected` 就是答案，防泄题）；**没有成绩的场次不显示 0 分**，
/// 改显示状态标签。
///
/// 该文件同时导出 [mineProgressProvider]，供「我的」首页与统计页复用。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/util/format.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../auth/auth_controller.dart';
import '../config/plan_save.dart' show PlanSaveSheet;
import '../home/models.dart';
import '../home/time_format.dart';
import '../homework/homework_page.dart' show HomeworkPage;
import '../mine/gamify_page.dart' show GamifyPage;
import '../srs/srs_page.dart' show SrsPage;
import 'my_stats_page.dart' show MyStatsPage;
import 'session_detail_page.dart';
import 'session_records.dart';
import 'settings_page.dart' show AboutPage, SettingsPage;

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
            const _RecordsSection(),
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
          const SizedBox(height: 12),
          _EntryTile(
            icon: Icons.repeat_outlined,
            title: '复习',
            subtitle: '间隔重复，巩固记忆',
            profile: profile,
            extra: extra,
            onTap: () => _push(context, const SrsPage()),
          ),
          const SizedBox(height: 12),
          _EntryTile(
            icon: Icons.bookmark_outline,
            title: '我的方案',
            subtitle: '保存与套用练习方案',
            profile: profile,
            extra: extra,
            onTap: () => _openPlans(context),
          ),
          const SizedBox(height: 12),
          _EntryTile(
            icon: Icons.emoji_events_outlined,
            title: '我的成就',
            subtitle: '积分 · 连续天数 · 徽章',
            profile: profile,
            extra: extra,
            onTap: () => _push(context, const GamifyPage()),
          ),
          const SizedBox(height: 12),
          _EntryTile(
            icon: Icons.assignment_outlined,
            title: '我的作业',
            subtitle: '班级作业与提交状态',
            profile: profile,
            extra: extra,
            onTap: () => _push(context, const HomeworkPage()),
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

  /// 「我的方案」从底部面板进入（自带 Material 祖先，与配置页同款入口）。
  ///
  /// 不传 [PlanSaveSheet.unit]：从「我的」页进来未必带单元上下文，面板会按当前
  /// 已配置练习的学科（[practiceConfigProvider] 的 `config.unit`）推导，二者皆无时
  /// 由面板自身的守卫拦下保存/套用。这里只负责把面板弹出来。
  void _openPlans(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (_) => const PlanSaveSheet(),
    );
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

/// F2 场次列表（`GET /api/sessions` —— 契约 §14.1 缺口 7 → 接口设计 §9.5）。
///
/// 四种状态都如实呈现：**加载中 / 失败（可重试）/ 空（引导去练习）/ 有数据**，
/// 任何一种都不拿假数据糊过去。
///
/// ⚠️ **进行中的场次不显示详情入口**：服务端对 `ongoing` 场次**拒绝**下发逐题明细
/// （`expected` 就是答案，中途返回等于泄题），点下去必然是一个错误弹窗。
/// 与其给一个走不通的入口，不如直接不显示（`SessionRecord.canOpenDetail`）。
class _RecordsSection extends ConsumerWidget {
  const _RecordsSection();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final state = ref.watch(sessionRecordsProvider);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text('练习记录', style: profile.cardTitleStyle)),
                if (state.total > 0)
                  Text(
                    '共 ${state.total} 次',
                    style: profile.captionStyle.copyWith(color: extra.textSub),
                  ),
                // ⚠️ 底部 Tab 是 `IndexedStack`（`main_shell.dart:39`，切回来**保留**已加载数据）
                // → 学生练完切回来，这份列表**不会自己刷新**。给一个显式入口，
                // 顺带也让错误态能原地重试（不必退出页面）。
                IconButton(
                  key: const ValueKey<String>('records-refresh'),
                  tooltip: '刷新',
                  onPressed: state.loading
                      ? null
                      : () => ref.read(sessionRecordsProvider.notifier).reload(),
                  icon: Icon(
                    Icons.refresh_rounded,
                    size: 20,
                    color: extra.textSub,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            ..._buildBody(context, ref, state, profile, extra),
          ],
        ),
      ),
    );
  }

  /// 列表主体（拆出来是为了让 `build` 只剩骨架，状态分支一眼看得完）。
  List<Widget> _buildBody(
    BuildContext context,
    WidgetRef ref,
    SessionRecordsState state,
    AgeProfile profile,
    AppExtraColors extra,
  ) {
    if (state.loading) {
      return const [
        Padding(
          padding: EdgeInsets.symmetric(vertical: 24),
          child: Center(child: LoadingView()),
        ),
      ];
    }

    if (state.errorText != null) {
      return [
        ErrorView(
          message: state.errorText!,
          onRetry: () => ref.read(sessionRecordsProvider.notifier).reload(),
        ),
      ];
    }

    if (state.items.isEmpty) {
      return [
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 16),
          child: Text(
            '还没有练习记录。去首页选一个单元，开始第一次练习吧。',
            style: profile.captionStyle.copyWith(color: extra.textSub),
          ),
        ),
      ];
    }

    final rows = <Widget>[];
    for (var i = 0; i < state.items.length; i++) {
      if (i > 0) {
        rows.add(Divider(height: 1, color: extra.divider));
      }
      rows.add(
        _RecordRow(record: state.items[i], profile: profile, extra: extra),
      );
    }

    if (state.hasMore) {
      rows.add(const SizedBox(height: 8));
      rows.add(
        Center(
          child: state.loadingMore
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : TextButton(
                  onPressed: () =>
                      ref.read(sessionRecordsProvider.notifier).loadMore(),
                  child: const Text('加载更多'),
                ),
        ),
      );
    }
    return rows;
  }
}

/// 一行场次：可点的进详情（右侧 chevron + 分数），进行中的不可点（右侧状态标签）。
class _RecordRow extends StatelessWidget {
  const _RecordRow({
    required this.record,
    required this.profile,
    required this.extra,
  });

  final SessionRecord record;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) {
    final r = record;
    final parts = <String>[];
    final when = formatLastPractice(r.startedAt);
    if (when != null) {
      parts.add(when);
    }
    if (r.durationMs > 0) {
      parts.add(formatDurationMs(r.durationMs));
    }
    parts.add(r.isChinese ? '${r.totalItems} 字' : '${r.totalItems} 个词');

    final content = Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(r.title, style: profile.bodyStyle),
                const SizedBox(height: 2),
                Text(
                  parts.join(' · '),
                  style: profile.captionStyle.copyWith(color: extra.textSub),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          _trailing(),
        ],
      ),
    );

    // 键固定在最外层，测试无论哪种状态都能按 `record-<id>` 定位到这一行。
    final key = ValueKey<String>('record-${r.sessionId}');
    if (!r.canOpenDetail) {
      return KeyedSubtree(key: key, child: content);
    }
    return InkWell(
      key: key,
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => SessionDetailPage(record: r)),
      ),
      child: content,
    );
  }

  Widget _trailing() {
    if (record.isOngoing) {
      // 进行中：没有成绩，也不给详情入口 —— 只给状态标签。
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: extra.divider,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Text(
          sessionStatusLabel(record.status),
          style: profile.captionStyle.copyWith(color: extra.textSub),
        ),
      );
    }
    final score = record.score;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(
              // `score` 为 null（理论上只有 ongoing）→ 显示「—」，**不拿 0 分冒充**。
              score == null ? '—' : formatScore(score),
              style: profile.wordStyle.copyWith(color: extra.hint),
            ),
            Text(
              formatPercent(record.accuracy),
              style: profile.captionStyle.copyWith(color: extra.textSub),
            ),
          ],
        ),
        Icon(Icons.chevron_right_rounded, color: extra.textFaint),
      ],
    );
  }
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
