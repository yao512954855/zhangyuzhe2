/// P5 练习配置页 —— F7 ~ F13（UI 规范 §7.5）。
///
/// 两条硬约束（UI 规范原文）：
/// - **所有选项用卡片，绝不用下拉框**；
/// - 顶部常驻「本次 N 题 · 约 M 分钟」。
///
/// 另外还有一个**规格之外的诚实要求**（见 `practice_strategy.dart` 文件头）：
/// 选项必须按学科给**真实能生效**的那些。英语的「选一课/选段」在服务端会被
/// 静默退化成整单元、语文的「数量/顺序」根本不被应用 —— 把这些选项画出来
/// 只会让学生以为功能坏了。宁可少给，不给假的。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme/age_profile.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../home/choice_card.dart';
import '../home/models.dart';
import 'practice_config_controller.dart';
import 'practice_estimate.dart';
import 'practice_launcher.dart';
import 'practice_strategy.dart';
import 'widgets/estimate_bar.dart';
import 'widgets/number_stepper.dart';
import 'widgets/text_picker.dart';

/// 进配置页的路由参数。
class PracticeConfigArgs {
  const PracticeConfigArgs({required this.unit, this.scope = PracticeScope.unit});

  final UnitItem unit;
  final PracticeScope scope;
}

class PracticeConfigPage extends ConsumerStatefulWidget {
  const PracticeConfigPage({
    super.key,
    this.unit,
    this.initialScope = PracticeScope.unit,
  });

  /// 目标单元。为 `null` 时说明是直接访问了路由（没带参数）——
  /// 页面会给出明确指引而不是崩掉。
  final UnitItem? unit;

  final PracticeScope initialScope;

  @override
  ConsumerState<PracticeConfigPage> createState() => _PracticeConfigPageState();
}

class _PracticeConfigPageState extends ConsumerState<PracticeConfigPage> {
  @override
  void initState() {
    super.initState();
    final unit = widget.unit;
    if (unit == null) {
      return;
    }
    // 放到首帧之后再初始化：`start()` 会写 provider 状态，
    // 在 initState 里同步写有可能撞上"构建期间修改状态"的断言。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        ref
            .read(practiceConfigProvider.notifier)
            .start(unit, scope: widget.initialScope);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(practiceConfigProvider);
    final config = state.config;

    if (widget.unit == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('设置这次怎么练')),
        body: const EmptyView(
          title: '请先在首页选一个单元',
          hint: '从单元卡上的「开始练习」进来，就能设置练法了',
          icon: Icons.touch_app_rounded,
        ),
      );
    }

    if (config == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('设置这次怎么练')),
        body: const LoadingView(),
      );
    }

    final estimate = estimatePractice(config, texts: state.texts);

    return Scaffold(
      appBar: AppBar(title: const Text('设置这次怎么练')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(
            AgeProfile.screenEdgeInset,
            AgeProfile.elementGap,
            AgeProfile.screenEdgeInset,
            AgeProfile.elementGap,
          ),
          children: [
            Text(
              '${config.unit.subject.label} · ${config.unit.name}',
              style: AgeScope.of(context).pageTitleStyle,
            ),
            const SizedBox(height: AgeProfile.elementGap),

            // ---- 一、练哪部分（F8）----
            ChoiceSection(
              title: '练哪部分',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  ChoiceGroup<PracticeScope>(
                    options: config.availableScopes,
                    selected: config.scope,
                    labelOf: (scope) => scope.label,
                    onSelect: (scope) =>
                        ref.read(practiceConfigProvider.notifier).setScope(scope),
                  ),
                  if (config.scope == PracticeScope.range) ...[
                    const SizedBox(height: 12),
                    NumberStepper(
                      caption: '从第',
                      value: config.rangeStart,
                      min: 1,
                      max: config.unit.total,
                      onChanged: (value) => ref
                          .read(practiceConfigProvider.notifier)
                          .setRange(start: value),
                    ),
                    const SizedBox(height: 10),
                    NumberStepper(
                      caption: '到第',
                      value: config.rangeEnd <= 0 ? config.unit.total : config.rangeEnd,
                      min: config.rangeStart,
                      max: config.unit.total,
                      onChanged: (value) => ref
                          .read(practiceConfigProvider.notifier)
                          .setRange(end: value),
                    ),
                  ],
                  if (config.scope == PracticeScope.lesson) ...[
                    const SizedBox(height: 12),
                    if (state.loadingTexts)
                      const LoadingView(message: '正在拿课文…')
                    else
                      TextPicker(
                        texts: state.texts,
                        selectedIds: config.selectedTextIds,
                        onToggle: (id) =>
                            ref.read(practiceConfigProvider.notifier).toggleText(id),
                        onSelectAll: (all) => ref
                            .read(practiceConfigProvider.notifier)
                            .setTextSelection(
                              all ? state.texts.map((t) => t.id).toSet() : <int>{},
                            ),
                      ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: AgeProfile.elementGap * 1.5),

            // ---- 二、练多少个 / 三、出题顺序（F9，语文不支持，见 getter 注释）----
            if (config.supportsCountAndOrder) ...[
              ChoiceSection(
                title: '练多少个',
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    ChoiceGroup<CountMode>(
                      options: CountMode.values,
                      selected: config.countMode,
                      labelOf: (mode) => mode.label,
                      onSelect: (mode) =>
                          ref.read(practiceConfigProvider.notifier).setCountMode(mode),
                    ),
                    if (config.countMode != CountMode.all) ...[
                      const SizedBox(height: 12),
                      NumberStepper(
                        caption: '数量',
                        value: config.count,
                        min: 1,
                        max: config.candidateCount <= 0 ? 1 : config.candidateCount,
                        onChanged: (value) =>
                            ref.read(practiceConfigProvider.notifier).setCount(value),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: AgeProfile.elementGap * 1.5),
              ChoiceSection(
                title: '出题顺序',
                hint: '默认按书上的顺序来',
                child: ChoiceGroup<OrderMode>(
                  options: OrderMode.values,
                  selected: config.order,
                  labelOf: (order) => order.label,
                  onSelect: (order) =>
                      ref.read(practiceConfigProvider.notifier).setOrder(order),
                ),
              ),
              const SizedBox(height: AgeProfile.elementGap * 1.5),
              ChoiceSection(
                title: '怎么问',
                child: ChoiceGroup<EnglishMode>(
                  options: EnglishMode.values,
                  selected: config.mode,
                  labelOf: (mode) => mode.label,
                  onSelect: (mode) =>
                      ref.read(practiceConfigProvider.notifier).setMode(mode),
                ),
              ),
              const SizedBox(height: AgeProfile.elementGap * 1.5),
            ],

            // ---- 四、时间限制（F11）----
            ChoiceSection(
              title: '时间限制',
              hint: config.isEnglish ? null : '整篇一口气背完，中途不掐表',
              child: ChoiceGroup<TimeLimit>(
                options: config.availableLimits,
                selected: config.timeLimit,
                labelOf: (limit) => limit.label,
                onSelect: (limit) =>
                    ref.read(practiceConfigProvider.notifier).setTimeLimit(limit),
              ),
            ),
            const SizedBox(height: AgeProfile.elementGap * 2),
          ],
        ),
      ),
      bottomNavigationBar: SafeArea(
        child: Container(
          padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
          color: Theme.of(context).scaffoldBackgroundColor,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // 底部错误提示：**不用 SnackBar**（会盖住主按钮，见 feedback.dart）
              if (state.errorText != null) ...[
                InlineNotice(
                  text: state.errorText!,
                  kind: NoticeKind.error,
                  onClose: () => ref.read(practiceConfigProvider.notifier).clearError(),
                ),
                const SizedBox(height: 12),
              ],
              EstimateBar(estimate: estimate, blockReason: config.blockReason),
              const SizedBox(height: 12),
              AppButton(
                label: '开始练习',
                loading: state.submitting,
                onPressed: config.canStart ? _submit : null,
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _submit() async {
    final session = await ref.read(practiceConfigProvider.notifier).submit();
    if (session == null || !mounted) {
      return;
    }
    // 跳练习页的逻辑与首页「快捷开始」共用一份（practice_launcher.dart）
    openPracticeSession(context, session);
  }
}
