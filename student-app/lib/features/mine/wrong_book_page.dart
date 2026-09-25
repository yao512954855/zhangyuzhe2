/// 错题本 —— WP-APP-05 F1（U1 / U2）。
///
/// 数据来自 `GET /api/me/wrong-words`（契约 §8.8，一期**只覆盖英语词条**，
/// 见契约 §14.1 缺口 6）：只返回 `wrong_count > 0` 的词条，按错误次数降序。
///
/// 页面按单元分组展示，每个单元组带一个「只练错题」按钮：复用
/// `practiceConfigProvider` 的 `start(unit, scope: wrong)` + `submit()`，
/// 走生产代码的 `PracticeConfig.toRequest()`（绝不在测试里手写请求体），
/// 再交给 `openPracticeSession` 进练习页。
///
/// ⚠️ 语文错题本后端尚无接口（契约 §14.1 缺口 6），页面底部如实说明，
/// **不给一个点了必报错的入口**（红线 4）。
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../config/practice_config_controller.dart';
import '../config/practice_strategy.dart';
import '../config/practice_launcher.dart';
import '../home/models.dart';

// ---------------------------------------------------------------------------
// 数据模型
// ---------------------------------------------------------------------------

/// 一条错题（`GET /api/me/wrong-words` 的 `items[]`）。
@immutable
class WrongWord {
  const WrongWord({
    required this.wordId,
    required this.unitId,
    required this.unitName,
    required this.type,
    this.en,
    this.cn,
    this.phonetic,
    this.wrongCount = 0,
    this.totalCount = 0,
    this.lastWrongAt,
    this.lastYourAnswer,
    this.masteryStatus,
  });

  factory WrongWord.fromJson(Map<String, dynamic> json) => WrongWord(
        wordId: _int(json['word_id']),
        unitId: _int(json['unit_id']),
        unitName: json['unit_name']?.toString() ?? '',
        type: json['type']?.toString() ?? 'word',
        en: json['en']?.toString(),
        cn: json['cn']?.toString(),
        phonetic: json['phonetic']?.toString(),
        wrongCount: _int(json['wrong_count']),
        totalCount: _int(json['total_count']),
        lastWrongAt: json['last_wrong_at']?.toString(),
        lastYourAnswer: json['last_your_answer']?.toString(),
        masteryStatus: json['mastery_status']?.toString(),
      );

  final int wordId;
  final int unitId;
  final String unitName;
  final String type;
  final String? en;
  final String? cn;
  final String? phonetic;
  final int wrongCount;
  final int totalCount;
  final String? lastWrongAt;
  final String? lastYourAnswer;
  final String? masteryStatus;

  /// 列表标题（中英都给，优先中文，没中文再退英文）。
  String get display => cn ?? en ?? '（缺少内容）';

  @override
  bool operator ==(Object other) =>
      other is WrongWord && other.wordId == wordId && other.unitId == unitId;

  @override
  int get hashCode => Object.hash(wordId, unitId);
}

/// 同一单元下的错题分组（F1「按单元列出」）。
@immutable
class WrongUnitGroup {
  const WrongUnitGroup({
    required this.unitId,
    required this.unitName,
    required this.items,
  });

  final int unitId;
  final String unitName;
  final List<WrongWord> items;

  /// 组内错误次数最高的一条，用来给单元排序（错得最多的单元排最前）。
  int get topWrong => items.isEmpty
      ? 0
      : items.map((w) => w.wrongCount).reduce((a, b) => a > b ? a : b);
}

/// 把错题按单元分组，并**保持服务端「错误次数降序」的全局顺序**：
/// 各单元组按组内最高错误次数降序排列，组内维持服务端原序。
///
/// 纯函数，U1 直接断言它的排序与「只含答错词条」。
List<WrongUnitGroup> groupByUnit(List<WrongWord> words) {
  final byUnit = <int, List<WrongWord>>{};
  for (final w in words) {
    (byUnit[w.unitId] ??= <WrongWord>[]).add(w);
  }
  final groups = byUnit.entries
      .map((e) => WrongUnitGroup(unitId: e.key, unitName: e.value.first.unitName, items: e.value))
      .toList();
  groups.sort((a, b) => b.topWrong.compareTo(a.topWrong));
  return groups;
}

/// 合并两次分页结果（按单元合并，单元内去重，保持原序）。
List<WrongUnitGroup> mergeGroups(List<WrongUnitGroup> base, List<WrongUnitGroup> more) {
  final map = <int, WrongUnitGroup>{
    for (final g in base) g.unitId: g,
  };
  for (final g in more) {
    final existing = map[g.unitId];
    if (existing == null) {
      map[g.unitId] = g;
    } else {
      final seen = <int>{for (final w in existing.items) w.wordId};
      final mergedItems = <WrongWord>[
        ...existing.items,
        for (final w in g.items)
          if (!seen.contains(w.wordId)) w,
      ];
      map[g.unitId] = WrongUnitGroup(
        unitId: g.unitId,
        unitName: g.unitName,
        items: mergedItems,
      );
    }
  }
  final result = map.values.toList();
  result.sort((a, b) => b.topWrong.compareTo(a.topWrong));
  return result;
}

// ---------------------------------------------------------------------------
// 状态与控制器
// ---------------------------------------------------------------------------

/// 错题本状态。
@immutable
class WrongBookState {
  const WrongBookState({
    this.groups = const [],
    this.total = 0,
    this.page = 1,
    this.loading = false,
    this.errorText,
    this.unitFilter,
  });

  final List<WrongUnitGroup> groups;
  final int total;
  final int page;
  final bool loading;
  final String? errorText;

  /// 按单元筛选（错题本页内顶部筛选用，null = 全部单元）。
  final int? unitFilter;

  bool get hasMore => page * _pageSize < total;

  bool get isEmpty => !loading && groups.isEmpty;

  WrongBookState copyWith({
    List<WrongUnitGroup>? groups,
    int? total,
    int? page,
    bool? loading,
    String? errorText,
    bool clearError = false,
    int? unitFilter,
  }) =>
      WrongBookState(
        groups: groups ?? this.groups,
        total: total ?? this.total,
        page: page ?? this.page,
        loading: loading ?? this.loading,
        errorText: clearError ? null : (errorText ?? this.errorText),
        unitFilter: unitFilter ?? this.unitFilter,
      );

  static const _pageSize = 100;
}

/// 错题本控制器（F1）。
///
/// 进入即拉第一页（页内已可覆盖绝大多数「一单元几十词」的场景），
/// 「加载更多」按契约 `page` / `page_size` 续拉并合并；可按单元筛选。
class WrongBookController extends Notifier<WrongBookState> {
  static const _pageSize = 100;

  @override
  WrongBookState build() {
    // 进入即加载；先给一个 loading 态，再异步补齐。
    _load(page: 1, replace: true);
    return const WrongBookState();
  }

  Future<void> _load({required int page, required bool replace}) async {
    state = state.copyWith(loading: true, clearError: true);
    try {
      final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
            '/api/me/wrong-words',
            query: <String, dynamic>{
              'page': page,
              'page_size': _pageSize,
              if (state.unitFilter != null) 'unit_id': state.unitFilter,
            },
          );
      final payload = data ?? const <String, dynamic>{};
      final items = _parseItems(payload['items']);
      final total = _int(payload['total']);
      final fetched = groupByUnit(items);
      final groups = replace ? fetched : mergeGroups(state.groups, fetched);
      state = state.copyWith(
        loading: false,
        groups: groups,
        total: total,
        page: page,
      );
    } on ApiException catch (e) {
      state = state.copyWith(loading: false, errorText: e.displayText);
    }
  }

  Future<void> reload() => _load(page: 1, replace: true);

  Future<void> loadMore() async {
    if (state.loading || !state.hasMore) {
      return;
    }
    await _load(page: state.page + 1, replace: false);
  }

  Future<void> setUnitFilter(int? unitId) async {
    if (unitId == state.unitFilter) {
      return;
    }
    state = state.copyWith(unitFilter: unitId);
    await _load(page: 1, replace: true);
  }
}

/// 错题本 Provider（全局单例，页面间不共享滚动但数据共享）。
final wrongBookProvider =
    NotifierProvider<WrongBookController, WrongBookState>(WrongBookController.new);

List<WrongWord> _parseItems(Object? raw) {
  if (raw is! List) {
    return const [];
  }
  final out = <WrongWord>[];
  for (final e in raw) {
    if (e is Map<String, dynamic>) {
      out.add(WrongWord.fromJson(e));
    }
  }
  return out;
}

int _int(Object? value) {
  if (value is int) {
    return value;
  }
  if (value is num) {
    return value.toInt();
  }
  return int.tryParse(value?.toString() ?? '') ?? 0;
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

/// 错题本页（F1）。
class WrongBookPage extends ConsumerWidget {
  const WrongBookPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(wrongBookProvider);
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(title: Text('错题本', style: profile.pageTitleStyle)),
      body: RefreshIndicator(
        onRefresh: () => ref.read(wrongBookProvider.notifier).reload(),
        child: _Body(state: state, profile: profile, extra: extra),
      ),
    );
  }
}

class _Body extends ConsumerWidget {
  const _Body({
    required this.state,
    required this.profile,
    required this.extra,
  });

  final WrongBookState state;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (state.loading && state.groups.isEmpty) {
      return const LoadingView();
    }
    if (state.errorText != null && state.groups.isEmpty) {
      return ErrorView(
        message: state.errorText!,
        onRetry: () => ref.read(wrongBookProvider.notifier).reload(),
      );
    }
    if (state.isEmpty) {
      return ListView(
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
        children: [
          const EmptyView(
            title: '还没有错题',
            hint: '练错的词会收在这里，方便你回头再练一遍',
            icon: Icons.sentiment_very_satisfied_outlined,
          ),
          const SizedBox(height: AgeProfile.elementGap),
          _chineseNote(extra),
        ],
      );
    }

    final children = <Widget>[];
    for (final group in state.groups) {
      children.add(_UnitGroupCard(group: group));
      children.add(const SizedBox(height: AgeProfile.elementGap));
    }
    if (state.hasMore) {
      children.add(
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Center(
            child: state.loading
                ? const CircularProgressIndicator()
                : TextButton(
                    onPressed: () => ref.read(wrongBookProvider.notifier).loadMore(),
                    child: Text('加载更多', style: profile.bodyStyle),
                  ),
          ),
        ),
      );
    }
    children.add(const SizedBox(height: AgeProfile.elementGap));
    children.add(_chineseNote(extra));

    return ListView(
      padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
      children: children,
    );
  }

  Widget _chineseNote(AppExtraColors extra) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: extra.card,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: extra.divider, width: 0.5),
        ),
        child: Text(
          '错题本目前支持英语；语文的错题整理还在准备中。',
          style: profile.captionStyle.copyWith(color: extra.textSub),
        ),
      );
}

/// 单个单元的错题分组卡。
class _UnitGroupCard extends ConsumerWidget {
  const _UnitGroupCard({required this.group});

  final WrongUnitGroup group;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(group.unitName, style: profile.cardTitleStyle),
                ),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: extra.hintLight,
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    '${group.items.length} 个易错词',
                    style: profile.captionStyle.copyWith(color: extra.hint),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            for (final word in group.items) ...[
              _WordRow(word: word, extra: extra, profile: profile),
              const SizedBox(height: 6),
            ],
            const SizedBox(height: 8),
            AppButton(
              label: '只练错题',
              kind: AppButtonKind.primary,
              onPressed: () => _practiceWrong(context, ref, group),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _practiceWrong(
    BuildContext context,
    WidgetRef ref,
    WrongUnitGroup group,
  ) async {
    // F1：「只练错题」——复用生产代码的组卷链路，scope='wrong'。
    final unit = UnitItem(
      unitId: group.unitId,
      subject: Subject.english,
      name: group.unitName,
      // 让 `PracticeConfig.canStart` 通过：本组确实有错词。
      withError: group.items.length,
      wordTotal: group.items.length,
    );
    final ctrl = ref.read(practiceConfigProvider.notifier);
    await ctrl.start(unit, scope: PracticeScope.wrong);
    final session = await ctrl.submit();
    if (!context.mounted) {
      return;
    }
    if (session == null) {
      // submit 失败（无错题 / 网络）已在 PracticeConfigController 内写入 errorText；
      // 这里给一个温和提示即可，不弹模态框（共通约束 #4）。
      showAppSnack(context, '这个单元暂时组不出错题，换个单元试试');
      return;
    }
    openPracticeSession(context, session);
  }
}

class _WordRow extends StatelessWidget {
  const _WordRow({
    required this.word,
    required this.extra,
    required this.profile,
  });

  final WrongWord word;
  final AppExtraColors extra;
  final AgeProfile profile;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(word.display, style: profile.bodyStyle),
              if (word.phonetic != null && word.phonetic!.isNotEmpty)
                Text(
                  word.phonetic!,
                  style: profile.captionStyle.copyWith(color: extra.textFaint),
                ),
            ],
          ),
        ),
        const SizedBox(width: 8),
        Text(
          '错 ${word.wrongCount} 次',
          style: profile.captionStyle.copyWith(color: extra.hint),
        ),
      ],
    );
  }
}
