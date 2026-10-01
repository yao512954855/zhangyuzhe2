/// 练习配置控制器 —— F7 ~ F13。
///
/// 职责边界：**只做状态与请求**，一个 Widget 都不碰。
/// 预估（F12）走 `practice_estimate.dart` 的纯函数，
/// 这样"改一个选项 → 预估跟着变"能被单测直接验证，不需要 pump 页面。
library;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../home/home_controller.dart';
import '../home/models.dart';
import 'practice_session.dart';
import 'practice_strategy.dart';

/// 配置页状态。
@immutable
class PracticeConfigState {
  const PracticeConfigState({
    this.config,
    this.texts = const <TextBrief>[],
    this.loadingTexts = false,
    this.submitting = false,
    this.errorText,
  });

  /// 当前配置；进入页面前为 `null`（页面应显示加载态而不是空指针）。
  final PracticeConfig? config;

  /// 语文课文列表（「选课文」需要）。
  final List<TextBrief> texts;

  final bool loadingTexts;

  /// 正在发起练习（按钮转圈、防重复提交）。
  final bool submitting;

  /// 发起失败的中文提示（已带 trace_id 编号）。
  final String? errorText;

  PracticeConfigState copyWith({
    PracticeConfig? config,
    List<TextBrief>? texts,
    bool? loadingTexts,
    bool? submitting,
    String? errorText,
    bool clearError = false,
  }) =>
      PracticeConfigState(
        config: config ?? this.config,
        texts: texts ?? this.texts,
        loadingTexts: loadingTexts ?? this.loadingTexts,
        submitting: submitting ?? this.submitting,
        errorText: clearError ? null : (errorText ?? this.errorText),
      );
}

class PracticeConfigController extends Notifier<PracticeConfigState> {
  @override
  PracticeConfigState build() => const PracticeConfigState();

  /// 进入配置（首页「开始练习」与单元详情「去练」都会调）。
  ///
  /// `scope` 允许调用方指定初始范围（比如"只练错题"快捷入口进来就是 `wrong`）。
  Future<void> start(UnitItem unit, {PracticeScope scope = PracticeScope.unit}) async {
    state = PracticeConfigState(
      config: PracticeConfig(
        unit: unit,
        scope: scope,
        // 自定义区间的默认值 = 整个单元（学生一进来就看到"1–24"，
        // 改成他想要的范围就行，不用先猜总共有多少题）
        rangeEnd: unit.total,
      ),
    );
    if (unit.subject == Subject.chinese) {
      await _loadTexts(unit);
    }
  }

  /// 拉语文课文列表（F8「选课文」+ F11 按篇限时都需要篇目信息）。
  Future<void> _loadTexts(UnitItem unit) async {
    state = state.copyWith(loadingTexts: true);
    try {
      final texts = await ref.read(homeRepositoryProvider).fetchTexts(unit.unitId);
      state = state.copyWith(texts: texts, loadingTexts: false);
    } on ApiException {
      // 拿不到课文列表不该让配置页不可用：仍可以"整个单元"背
      state = state.copyWith(loadingTexts: false);
    }
  }

  // ------------------------------------------------------------ 五个维度

  void setScope(PracticeScope scope) {
    final config = state.config;
    if (config == null) {
      return;
    }
    // 切到「选课文」而还没选过 → 默认全选（学生再取消不要的，
    // 比"一个都没选、按钮是灰的"更不容易卡住）
    final selected = scope == PracticeScope.lesson && config.selectedTextIds.isEmpty
        ? state.texts.map((t) => t.id).toSet()
        : config.selectedTextIds;
    _apply(config.copyWith(scope: scope, selectedTextIds: selected));
  }

  void toggleText(int textId) {
    final config = state.config;
    if (config == null) {
      return;
    }
    final next = Set<int>.from(config.selectedTextIds);
    if (!next.remove(textId)) {
      next.add(textId);
    }
    _apply(config.copyWith(selectedTextIds: next));
  }

  void setTextSelection(Set<int> ids) {
    final config = state.config;
    if (config == null) {
      return;
    }
    _apply(config.copyWith(selectedTextIds: ids));
  }

  void setRange({int? start, int? end}) {
    final config = state.config;
    if (config == null) {
      return;
    }
    _apply(config.copyWith(rangeStart: start, rangeEnd: end));
  }

  void setCountMode(CountMode mode) {
    final config = state.config;
    if (config == null) {
      return;
    }
    _apply(config.copyWith(countMode: mode));
  }

  void setCount(int count) {
    final config = state.config;
    if (config == null) {
      return;
    }
    final total = config.candidateCount;
    final capped = count.clamp(1, total <= 0 ? 1 : total);
    _apply(config.copyWith(count: capped));
  }

  void setOrder(OrderMode order) {
    final config = state.config;
    if (config == null) {
      return;
    }
    _apply(config.copyWith(order: order));
  }

  void setMode(EnglishMode mode) {
    final config = state.config;
    if (config == null) {
      return;
    }
    _apply(config.copyWith(mode: mode));
  }

  void setTimeLimit(TimeLimit limit) {
    final config = state.config;
    if (config == null) {
      return;
    }
    _apply(config.copyWith(timeLimit: limit));
  }

  /// 把配置写回状态，并**顺手清掉上一次的错误提示**
  /// （学生一改选项，那条红字就该消失，否则会以为改了也没用）。
  void _apply(PracticeConfig config) {
    state = state.copyWith(config: config, clearError: true);
  }

  // ------------------------------------------------------------ 发起练习

  /// F13：`POST /api/sessions` 拿到题序。
  ///
  /// 成功返回场次（页面据此跳练习页）；失败返回 `null`，
  /// 中文原因已写进 `state.errorText`。
  Future<PracticeSession?> submit() async {
    final config = state.config;
    if (config == null || state.submitting) {
      return null;
    }
    if (!config.canStart) {
      state = state.copyWith(errorText: config.blockReason ?? '这次还练不了');
      return null;
    }

    state = state.copyWith(submitting: true, clearError: true);
    try {
      final body = config.toRequest();
      final data = await ref.read(apiClientProvider).post<Map<String, dynamic>>(
            '/api/sessions',
            body: body,
          );
      // 带上原请求体：结算页「再练一次」按**同一配置**重开（真重开，不回首页）。
      final session = PracticeSession.fromJson(data ?? const <String, dynamic>{})
          .withReplay(body);
      if (session.isEmpty) {
        state = state.copyWith(
          submitting: false,
          errorText: '这个范围里没有题目，换个范围试试',
        );
        return null;
      }
      state = state.copyWith(submitting: false);
      return session;
    } on ApiException catch (e) {
      state = state.copyWith(submitting: false, errorText: e.displayText);
      return null;
    }
  }

  /// 关掉错误提示（**不能顺手 reset** —— 那会把学生刚设好的一整页配置清掉，
  /// 他只是想关掉一条提示，不是想重来）。
  void clearError() {
    state = state.copyWith(clearError: true);
  }

  /// 离开配置页时清空（避免下次从别的单元进来还带着上一个单元的配置）。
  void reset() {
    state = const PracticeConfigState();
  }
}

final practiceConfigProvider =
    NotifierProvider<PracticeConfigController, PracticeConfigState>(
  PracticeConfigController.new,
);
