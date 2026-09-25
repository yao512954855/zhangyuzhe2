/// P4 首页 —— F1 / F2 / F4 / F5 / F6 / F14 / F15（UI 规范 §7.4）。
///
/// 页面自身的三条规则：
/// 1. **没有选过年级学期 → 整页就是引导页**（F2）。
///    不弹窗、不 push 新路由 —— 靠结构保证"强制"，学生按返回键也绕不过去。
/// 2. 学科切换**纯本地过滤**（数据一次拉全），不闪、不转圈（§5.1 禁止闪烁）。
/// 3. 断网时展示缓存并明说"这是上次的数据"（U10），**不白屏**。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/feedback.dart';
import '../../router/routes/home.dart';
import '../../router/routes/mistakes.dart';
import '../auth/auth_controller.dart';
import '../config/practice_config_page.dart';
import '../config/practice_launcher.dart';
import '../config/practice_config_controller.dart';
import '../config/practice_strategy.dart';
import '../theme/theme_switcher_sheet.dart';
import 'choice_card.dart';
import 'grade_semester_picker.dart';
import 'home_controller.dart';
import 'models.dart';
import 'progress_header.dart';
import 'unit_card.dart';

class HomePage extends ConsumerStatefulWidget {
  const HomePage({super.key, this.now});

  /// 可注入的"当前时间"（让单元卡的"上次：今天/昨天"在单测里稳定）。
  final DateTime? now;

  @override
  ConsumerState<HomePage> createState() => _HomePageState();
}

class _HomePageState extends ConsumerState<HomePage> {
  /// 正在快捷开练的单元 id（防连点：一次点击会发一次组卷请求）。
  int? _startingUnitId;

  @override
  Widget build(BuildContext context) {
    final preference = ref.watch(gradePrefProvider);

    // F2：首次引导。整页交给选择器，选完 preference 一变就自动渲染出真首页
    if (!preference.isSet) {
      return GradeSemesterPickerPage(force: true, clock: widget.now);
    }

    final asyncData = ref.watch(homeControllerProvider);
    final subject = ref.watch(homeSubjectProvider);

    return Scaffold(
      body: SafeArea(
        child: asyncData.when(
          loading: () => const LoadingView(),
          error: (error, _) => ErrorView.fromException(
            error is ApiException ? error : ApiException.server(error),
            onRetry: () => ref.read(homeControllerProvider.notifier).refresh(),
          ),
          data: (data) => _content(data, subject),
        ),
      ),
    );
  }

  Widget _content(HomeData data, Subject subject) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final auth = ref.watch(authControllerProvider);
    final units = data.units.bySubject(subject);

    return RefreshIndicator(
      onRefresh: () => ref.read(homeControllerProvider.notifier).refresh(),
      child: ListView(
        // 内容不足一屏时也要能下拉刷新
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
        children: [
          ProgressHeader(
            displayName: auth.user?.displayName ?? '同学',
            preference: ref.watch(gradePrefProvider),
            subject: subject,
            progress: data.progress,
            preferencePending: data.preferencePending,
            offline: data.offline,
            onTapGrade: () => Navigator.of(context).pushNamed(HomeRoutes.gradePicker),
            onTapSettings: _openSettings,
          ),
          const SizedBox(height: AgeProfile.elementGap),

          // F4：学科切换（卡片式，与其它选择视觉统一；不是下拉框）
          ChoiceGroup<Subject>(
            options: Subject.values,
            selected: subject,
            labelOf: (value) => value.label,
            onSelect: (value) => ref.read(homeSubjectProvider.notifier).select(value),
          ),
          const SizedBox(height: AgeProfile.elementGap),

          // F5：单元列表
          if (units.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 40),
              child: EmptyView(
                title: '这个年级还没有${subject.label}内容',
                hint: data.offline
                    ? '连上服务器后再看看'
                    : '让管理者先导入${subject.label}内容，就能开始练了',
                icon: Icons.inbox_rounded,
              ),
            )
          else
            for (final unit in units) ...[
              UnitCard(
                unit: unit,
                now: widget.now,
                onTap: () => Navigator.of(context)
                    .pushNamed(HomeRoutes.unitDetail, arguments: unit),
                onStart: () => _quickStart(unit),
              ),
              const SizedBox(height: AgeProfile.elementGap),
            ],

          // P4 的常驻快捷入口：「只练错题（N 个）」
          _WrongWordsCard(
            count: data.wrongCount,
            subject: subject,
            onTap: () => _openWrongPicker(data, subject),
          ),
          const SizedBox(height: 8),
          Center(
            child: Text(
              '练完一轮记得看看远处，让眼睛歇一会儿',
              style: profile.captionStyle.copyWith(color: extra.textFaint),
            ),
          ),
        ],
      ),
    );
  }

  /// F6：单元卡「开始练习」——**默认组合直接开练，不进配置页**。
  ///
  /// 默认组合 = 全部 + 顺序 + 不限时 + 中文→英文，
  /// 正好是 [PracticeConfig] 的字段默认值（改默认值会同时改这里，是刻意的）。
  Future<void> _quickStart(UnitItem unit) async {
    if (_startingUnitId != null) {
      return;
    }
    setState(() => _startingUnitId = unit.unitId);
    final notifier = ref.read(practiceConfigProvider.notifier);
    try {
      await notifier.start(unit);
      final session = await notifier.submit();
      if (!mounted) {
        return;
      }
      if (session == null) {
        final message = ref.read(practiceConfigProvider).errorText ?? '这次没组出题目，换一个单元试试';
        showAppSnack(context, message);
        return;
      }
      openPracticeSession(context, session);
    } finally {
      if (mounted) {
        setState(() => _startingUnitId = null);
      }
    }
  }

  /// 「只练错题」：**先选单元**再进配置页。
  ///
  /// 为什么不直接开练：错题是按单元组织的，而首页拿到的单元列表里
  /// **没有 `with_error` 字段**（契约 §8.3 未下发，见 偏离 #44），
  /// 所以客户端无法判断哪个单元有错题。与其猜一个，不如让学生点一下 ——
  /// 选中的单元如果确实没错题，配置页会明确告诉他"这个单元还没有错题"。
  Future<void> _openWrongPicker(HomeData data, Subject subject) async {
    // 兜底：语文侧没有错题数据（错题本一期只覆盖英语词条），
    // 组卷时的 scope=wrong 会被后端直接拒掉 —— 不要让学生走到那一步。
    if (subject != Subject.english) {
      showAppSnack(context, '语文错题本还没上线，先按课文背吧');
      return;
    }
    final units = data.units.bySubject(subject);
    if (units.isEmpty) {
      showAppSnack(context, '这个年级还没有${subject.label}内容');
      return;
    }

    final picked = await showModalBottomSheet<UnitItem>(
      context: context,
      showDragHandle: true,
      builder: (sheetContext) {
        final profile = AgeScope.of(sheetContext);
        return SafeArea(
          child: ListView(
            shrinkWrap: true,
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            children: [
              Text('练哪个单元的错题？', style: profile.cardTitleStyle),
              const SizedBox(height: 12),
              for (final unit in units)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: ChoiceCard(
                    label: '${unit.name}（${unit.progressText}）',
                    selected: false,
                    expand: true,
                    onTap: () => Navigator.of(sheetContext).pop(unit),
                  ),
                ),
            ],
          ),
        );
      },
    );

    if (picked == null || !mounted) {
      return;
    }
    Navigator.of(context).pushNamed(
      HomeRoutes.practiceConfig,
      arguments: PracticeConfigArgs(unit: picked, scope: PracticeScope.wrong),
    );
  }

  /// 设置入口：APP-05 的 P12 设置页落地后自动接管；
  /// 在此之前退化成"换个颜色"（APP-01 已有的能力），
  /// **不留一个点了没反应的按钮**。
  void _openSettings() {
    final settingsRoute = MistakesRoutes.settings;
    if (MistakesRoutes.routes.containsKey(settingsRoute)) {
      Navigator.of(context).pushNamed(settingsRoute);
      return;
    }
    showThemeSwitcher(context);
  }
}

/// 「只练错题（N 个）」常驻卡（UI 规范 P4 明确要求"不用进二级页面"）。
///
/// ⚠️ **错题本一期只覆盖英语词条**（`backend/app/api/me_learning.py` 模块说明
/// 「契约 §14.1 缺口 6，一期只覆盖英语词条」），语文侧没有错题数据。
/// 语文 Tab 上必须**如实说明还没上线**，而不是显示一个英语的词数 ——
/// 更不能给一个点下去必然被后端拒绝的入口。
class _WrongWordsCard extends StatelessWidget {
  const _WrongWordsCard({required this.count, required this.subject, required this.onTap});

  final int count;
  final Subject subject;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final available = subject == Subject.english;
    final enabled = available && count > 0;

    final title = !available
        ? '语文错题本还没上线'
        : (enabled ? '只练错题（$count 个）' : '只练错题');
    final hint = !available
        ? '先按课文背，背过的篇目会打上标记'
        : (enabled ? '把做错过的词再练一遍' : '还没有错题，先正常练几遍吧');

    return Material(
      color: extra.card,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: enabled ? onTap : null,
        child: Container(
          constraints: BoxConstraints(minHeight: profile.listItemHeight + 8),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: extra.divider, width: 0.5),
          ),
          child: Row(
            children: [
              Icon(
                Icons.error_outline_rounded,
                size: profile.caption + 8,
                color: enabled ? extra.error : extra.textFaint,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: profile.bodyStyle.copyWith(
                        fontWeight: FontWeight.w500,
                        color: enabled ? null : extra.textFaint,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      hint,
                      style: profile.captionStyle.copyWith(color: extra.textSub),
                    ),
                  ],
                ),
              ),
              Icon(
                Icons.chevron_right_rounded,
                size: profile.caption + 8,
                color: enabled ? extra.textFaint : extra.divider,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
