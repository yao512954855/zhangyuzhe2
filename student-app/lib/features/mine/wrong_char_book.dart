/// 语文错题本 —— A5（`GET /api/me/wrong-chars`，接口设计 §8.10）。
///
/// **它与英语错题本（§8.8）是两个端点、两套字段**，不是同一份 `items`
/// 再按 `subject` 分叉：英语错题是一条**词条**（`en` / `cn` / `phonetic`），
/// 语文错题是**「课文 + 一个漏过的字」**，字段没有交集。
/// 硬塞进一个端点，客户端就得在同一个列表上写两套解析 ——
/// 接口设计里最容易长歪的一类。
///
/// 「重背这篇」**不新增接口**：复用 `POST /api/sessions`（§9.1），
/// 传 `scope='lesson'` + `scope_detail.text_ids=[text_id]`，
/// 与配置页「选课文」走的是**同一条生产链路**（`PracticeConfig.toRequest()`）。
///
/// ⚠️ **按钮写「重背这篇」，不写「只练错题」**：背诵是按课文逐字往下走的，
/// 后端没有「只背这几个字」这种模式。写成「只练错题」就是骗学生 ——
/// 他点下去会发现整篇都得重背。宁可把话说准（红线 4）。
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
import '../config/practice_config_controller.dart';
import '../config/practice_launcher.dart';
import '../config/practice_strategy.dart';
import '../home/models.dart';

// ---------------------------------------------------------------------------
// 数据模型
// ---------------------------------------------------------------------------

/// 一条错字（`GET /api/me/wrong-chars` 的 `items[]`）。
@immutable
class WrongChar {
  const WrongChar({
    required this.textId,
    required this.textTitle,
    required this.unitId,
    required this.unitName,
    required this.char,
    this.wrongCount = 0,
    this.lastWrongAt,
    this.firstPos = 0,
    this.contextBefore = '',
    this.contextAfter = '',
  });

  factory WrongChar.fromJson(Map<String, dynamic> json) => WrongChar(
        textId: _int(json['text_id']),
        textTitle: json['text_title']?.toString() ?? '',
        unitId: _int(json['unit_id']),
        unitName: json['unit_name']?.toString() ?? '',
        char: json['char']?.toString() ?? '',
        wrongCount: _int(json['wrong_count']),
        lastWrongAt: json['last_wrong_at']?.toString(),
        firstPos: _int(json['first_pos']),
        contextBefore: json['context_before']?.toString() ?? '',
        contextAfter: json['context_after']?.toString() ?? '',
      );

  final int textId;
  final String textTitle;
  final int unitId;
  final String unitName;

  /// 漏过的那个字。
  final String char;
  final int wrongCount;
  final String? lastWrongAt;

  /// 该字在 `content_plain` 里的下标（服务端给，客户端不自己算）。
  final int firstPos;

  /// 上下文由**服务端**从 `content_plain` 截好（前后各 4 字）——
  /// 客户端再实现一遍取字逻辑，就是第二份真相。
  final String contextBefore;
  final String contextAfter;

  @override
  bool operator ==(Object other) =>
      other is WrongChar && other.textId == textId && other.char == char;

  @override
  int get hashCode => Object.hash(textId, char);
}

/// 同一篇课文下的错字分组。
@immutable
class WrongTextGroup {
  const WrongTextGroup({
    required this.textId,
    required this.textTitle,
    required this.unitId,
    required this.unitName,
    required this.items,
  });

  final int textId;
  final String textTitle;
  final int unitId;
  final String unitName;
  final List<WrongChar> items;

  /// 组内最高错误次数，用来给课文排序（错得最多的排最前）。
  int get topWrong => items.isEmpty
      ? 0
      : items.map((w) => w.wrongCount).reduce((a, b) => a > b ? a : b);
}

/// 把错字按课文分组，并**保持服务端「错次降序」的全局顺序**。
///
/// 纯函数，测试直接断言它的分组与排序。
List<WrongTextGroup> groupByText(List<WrongChar> chars) {
  final byText = <int, List<WrongChar>>{};
  for (final w in chars) {
    (byText[w.textId] ??= <WrongChar>[]).add(w);
  }
  final groups = byText.entries
      .map(
        (e) => WrongTextGroup(
          textId: e.key,
          textTitle: e.value.first.textTitle,
          unitId: e.value.first.unitId,
          unitName: e.value.first.unitName,
          items: e.value,
        ),
      )
      .toList();
  groups.sort((a, b) => b.topWrong.compareTo(a.topWrong));
  return groups;
}

/// 合并两次分页结果（按课文合并，课文内按字去重，保持原序）。
List<WrongTextGroup> mergeTextGroups(
  List<WrongTextGroup> base,
  List<WrongTextGroup> more,
) {
  final map = <int, WrongTextGroup>{for (final g in base) g.textId: g};
  for (final g in more) {
    final existing = map[g.textId];
    if (existing == null) {
      map[g.textId] = g;
    } else {
      final seen = <String>{for (final w in existing.items) w.char};
      map[g.textId] = WrongTextGroup(
        textId: g.textId,
        textTitle: g.textTitle,
        unitId: g.unitId,
        unitName: g.unitName,
        items: <WrongChar>[
          ...existing.items,
          for (final w in g.items)
            if (!seen.contains(w.char)) w,
        ],
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

@immutable
class WrongCharsState {
  const WrongCharsState({
    this.groups = const [],
    this.total = 0,
    this.page = 1,
    this.loading = false,
    this.errorText,
  });

  final List<WrongTextGroup> groups;
  final int total;
  final int page;
  final bool loading;
  final String? errorText;

  bool get hasMore => page * _pageSize < total;

  bool get isEmpty => !loading && groups.isEmpty;

  WrongCharsState copyWith({
    List<WrongTextGroup>? groups,
    int? total,
    int? page,
    bool? loading,
    String? errorText,
    bool clearError = false,
  }) =>
      WrongCharsState(
        groups: groups ?? this.groups,
        total: total ?? this.total,
        page: page ?? this.page,
        loading: loading ?? this.loading,
        errorText: clearError ? null : (errorText ?? this.errorText),
      );

  static const _pageSize = 100;
}

/// 语文错题本控制器。
class WrongCharsController extends Notifier<WrongCharsState> {
  static const _pageSize = 100;

  @override
  WrongCharsState build() {
    // ⚠️ 首次加载必须 `Future.microtask`：在 `build()` 里同步读 `state`
    // 会抛「Tried to read the state of an uninitialized provider」。
    Future.microtask(() => _load(page: 1, replace: true));
    return const WrongCharsState();
  }

  Future<void> _load({required int page, required bool replace}) async {
    state = state.copyWith(loading: true, clearError: true);
    try {
      final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
            '/api/me/wrong-chars',
            query: <String, dynamic>{'page': page, 'page_size': _pageSize},
          );
      final payload = data ?? const <String, dynamic>{};
      final fetched = groupByText(_parseItems(payload['items']));
      state = state.copyWith(
        loading: false,
        groups: replace ? fetched : mergeTextGroups(state.groups, fetched),
        total: _int(payload['total']),
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
}

final wrongCharsProvider =
    NotifierProvider<WrongCharsController, WrongCharsState>(WrongCharsController.new);

List<WrongChar> _parseItems(Object? raw) {
  if (raw is! List) {
    return const [];
  }
  final out = <WrongChar>[];
  for (final e in raw) {
    if (e is Map<String, dynamic>) {
      out.add(WrongChar.fromJson(e));
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
// 页面片段（挂在 `WrongBookPage` 的「语文」页签里）
// ---------------------------------------------------------------------------

class WrongCharsView extends ConsumerWidget {
  const WrongCharsView({super.key, this.now});

  /// 可注入的「当前时间」（让「今天 / 昨天」在单测里稳定）。
  final DateTime? now;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(wrongCharsProvider);
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return RefreshIndicator(
      onRefresh: () => ref.read(wrongCharsProvider.notifier).reload(),
      child: _Body(state: state, profile: profile, extra: extra, now: now),
    );
  }
}

class _Body extends ConsumerWidget {
  const _Body({
    required this.state,
    required this.profile,
    required this.extra,
    this.now,
  });

  final WrongCharsState state;
  final AgeProfile profile;
  final AppExtraColors extra;
  final DateTime? now;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (state.loading && state.groups.isEmpty) {
      return const LoadingView();
    }
    if (state.errorText != null && state.groups.isEmpty) {
      return ErrorView(
        message: state.errorText!,
        onRetry: () => ref.read(wrongCharsProvider.notifier).reload(),
      );
    }
    if (state.isEmpty) {
      return ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
        children: const [
          EmptyView(
            title: '还没有漏背的字',
            hint: '背诵时漏掉的字会收在这里，方便你回头再背一遍',
            icon: Icons.sentiment_very_satisfied_outlined,
          ),
        ],
      );
    }

    final children = <Widget>[];
    for (final group in state.groups) {
      children.add(_TextGroupCard(group: group, now: now));
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
                    onPressed: () => ref.read(wrongCharsProvider.notifier).loadMore(),
                    child: Text('加载更多', style: profile.bodyStyle),
                  ),
          ),
        ),
      );
    }

    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
      children: children,
    );
  }
}

/// 一篇课文的错字分组卡。
class _TextGroupCard extends ConsumerWidget {
  const _TextGroupCard({required this.group, this.now});

  final WrongTextGroup group;
  final DateTime? now;

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
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('《${group.textTitle}》', style: profile.cardTitleStyle),
                      const SizedBox(height: 2),
                      Text(
                        group.unitName,
                        style: profile.captionStyle.copyWith(color: extra.textFaint),
                      ),
                    ],
                  ),
                ),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: extra.hintLight,
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    '${group.items.length} 个易错字',
                    style: profile.captionStyle.copyWith(color: extra.hint),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            for (final w in group.items) ...[
              _CharRow(item: w, profile: profile, extra: extra, now: now),
              const SizedBox(height: 8),
            ],
            const SizedBox(height: 4),
            AppButton(
              label: '重背这篇',
              kind: AppButtonKind.primary,
              onPressed: () => _reciteText(context, ref, group),
            ),
          ],
        ),
      ),
    );
  }

  /// F1 语文侧：「重背这篇」—— 复用生产组卷链路（`scope=lesson` + `text_ids`）。
  Future<void> _reciteText(
    BuildContext context,
    WidgetRef ref,
    WrongTextGroup group,
  ) async {
    final unit = UnitItem(
      unitId: group.unitId,
      subject: Subject.chinese,
      name: group.unitName,
      // `canStart` 只看选中篇数，这里给 1 篇是为了让预估用量说得通。
      textTotal: 1,
    );
    final ctrl = ref.read(practiceConfigProvider.notifier);
    await ctrl.start(unit, scope: PracticeScope.lesson);
    ctrl.setTextSelection(<int>{group.textId});
    final session = await ctrl.submit();
    if (!context.mounted) {
      return;
    }
    if (session == null) {
      showAppSnack(context, '这篇暂时开不了，过会儿再试');
      return;
    }
    openPracticeSession(context, session);
  }
}

class _CharRow extends StatelessWidget {
  const _CharRow({
    required this.item,
    required this.profile,
    required this.extra,
    this.now,
  });

  final WrongChar item;
  final AgeProfile profile;
  final AppExtraColors extra;
  final DateTime? now;

  @override
  Widget build(BuildContext context) {
    final day = formatRelativeDay(item.lastWrongAt, now: now);
    final times = day.isEmpty ? '漏 ${item.wrongCount} 次' : '漏 ${item.wrongCount} 次 · $day';

    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        _CharBox(char: item.char, profile: profile, extra: extra),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text.rich(
                TextSpan(
                  children: [
                    TextSpan(
                      text: item.contextBefore,
                      style: profile.bodyStyle.copyWith(color: extra.textFaint),
                    ),
                    TextSpan(
                      text: item.char,
                      style: profile.bodyStyle.copyWith(
                        color: extra.hint,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    TextSpan(
                      text: item.contextAfter,
                      style: profile.bodyStyle.copyWith(color: extra.textFaint),
                    ),
                  ],
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              Text(times, style: profile.captionStyle.copyWith(color: extra.hint)),
            ],
          ),
        ),
      ],
    );
  }
}

/// 字格 —— 与背诵页「当前位置」同一种视觉，学生一眼认得出这是「一个字」。
class _CharBox extends StatelessWidget {
  const _CharBox({
    required this.char,
    required this.profile,
    required this.extra,
  });

  final String char;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 44,
      height: 44,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        // 卡片色 + 细描边 = 一个「格子」，与背诵页的字格同一种语言，
        // 学生一眼认得出这是「一个字」而不是普通文字。
        color: extra.card,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: extra.divider, width: 0.5),
      ),
      child: Text(
        char,
        // 不给颜色：`profile.*Style` 本身不带色，继承主题正文色，
        // 三套护眼主题下都自动正确（硬写一个色值就会在某个主题上出问题）。
        style: profile.cardTitleStyle,
      ),
    );
  }
}
