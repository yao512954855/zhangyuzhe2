/// 听背 · 选单元页 —— todo11 A1/A2（契约 v1.3 §4.1）。
///
/// 流程：`GET /api/units` 拉两级单元树 → 一级分组可展开，二级单元带勾选框
/// （一级行勾选 = 全选其下二级；无二级的一级按叶子直接勾选）→ 类型多选
/// （checkbox，默认全不选、至少选一，可用条数按所选单元求和）
/// → 底部栏「已选 N 个单元 · 共 X 条」+【听】【背】。
/// start 请求体数组化：`{mode, unit_ids:[...], types:[...]}`（数组顺序 =
/// 勾选顺序；只勾 1 个也发单元素数组，不发单数字段）。
/// 【听】跳听写链路（WP-D 页面，mode=listen 走同一组卷契约）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../../features/dictation/dictation_models.dart';
import '../../router/routes/dictation.dart';
import '../../router/routes/home.dart';
import '../../router/routes/recite.dart';
import 'practice_entry.dart';

/// `/api/units` 一个节点（契约 §4.1 v1.3：
/// `{id,name,sort,parent_id,counts:{...},children:[...]}`，一级 counts 含二级汇总）。
class UnitInfo {
  const UnitInfo({
    required this.id,
    required this.name,
    required this.sort,
    required this.counts,
    this.parentId,
    this.children = const <UnitInfo>[],
  });

  factory UnitInfo.fromJson(Map<String, dynamic> json) => UnitInfo(
        id: (json['id'] as num?)?.toInt() ?? 0,
        name: json['name']?.toString() ?? '',
        sort: (json['sort'] as num?)?.toInt() ?? 0,
        parentId: (json['parent_id'] as num?)?.toInt(),
        counts: <String, int>{
          'word': _countOf(json, 'word'),
          'phrase': _countOf(json, 'phrase'),
          'sentence': _countOf(json, 'sentence'),
        },
        children: ((json['children'] as List?) ?? const [])
            .whereType<Map>()
            .map((m) => UnitInfo.fromJson(Map<String, dynamic>.from(m)))
            .toList(),
      );

  static int _countOf(Map<String, dynamic> json, String key) {
    final counts = json['counts'];
    if (counts is! Map) {
      return 0;
    }
    final v = counts[key];
    if (v is int) {
      return v;
    }
    if (v is num) {
      return v.toInt();
    }
    return int.tryParse(v?.toString() ?? '') ?? 0;
  }

  final int id;
  final String name;
  final int sort;
  final int? parentId;

  /// `word` / `phrase` / `sentence` → 条数。
  final Map<String, int> counts;

  /// 二级单元（空 = 该节点自己就是可勾选的叶子单元）。
  final List<UnitInfo> children;

  bool get isGroup => children.isNotEmpty;

  int countOf(String type) => counts[type] ?? 0;

  int get totalCount => counts.values.fold<int>(0, (a, b) => a + b);
}

/// 听背 · 选单元页。
class UnitListPage extends ConsumerStatefulWidget {
  const UnitListPage({super.key});

  @override
  ConsumerState<UnitListPage> createState() => _UnitListPageState();
}

class _UnitListPageState extends ConsumerState<UnitListPage> {
  static const _typeLabels = <String, String>{
    'word': '单词',
    'phrase': '词组',
    'sentence': '短句',
  };

  List<UnitInfo>? _units;
  Object? _error;

  /// 展开的一级分组 id 集合（默认全展开）。
  final Set<int> _expanded = <int>{};

  /// 已勾选的**叶子单元** id（LinkedHashSet：顺序 = 勾选顺序，进请求体）。
  final Set<int> _selectedUnitIds = <int>{};

  /// 已勾选类型（§7 枚举；顺序 = 勾选顺序，进请求体）。
  final Set<String> _selectedTypes = <String>{};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    setState(() {
      _error = null;
    });
    try {
      final data =
          await ref.read(apiClientProvider).get<List<dynamic>>('/api/units');
      final units = (data ?? const [])
          .whereType<Map>()
          .map((m) => UnitInfo.fromJson(Map<String, dynamic>.from(m)))
          .toList()
        ..sort((a, b) => a.sort.compareTo(b.sort));
      if (!mounted) {
        return;
      }
      setState(() {
        _units = units;
        _unitById = <int, UnitInfo>{
          for (final node in units)
            for (final leaf in _leafUnitsOf(node)) leaf.id: leaf,
        };
        _expanded
          ..clear()
          ..addAll(units.where((u) => u.isGroup).map((u) => u.id));
      });
    } on Object catch (e) {
      if (mounted) {
        setState(() => _error = e);
      }
    }
  }

  /// 可勾选的叶子单元（一级分组之下是二级；无二级的一级自己就是叶子）。
  List<UnitInfo> _leafUnitsOf(UnitInfo node) =>
      node.isGroup ? node.children : <UnitInfo>[node];

  /// id → 叶子单元（供按勾选顺序取回；随每次加载重建）。
  Map<int, UnitInfo> _unitById = const <int, UnitInfo>{};

  /// 当前勾选的叶子单元（**顺序 = 勾选顺序**，进 start 请求体 unit_ids）。
  List<UnitInfo> get _selectedUnits => [
        for (final id in _selectedUnitIds)
          if (_unitById[id] != null) _unitById[id]!,
      ];

  /// 所选类型在某单元上的条数求和（F15-3：类型可用条数按所选单元求和）。
  int _typeCount(String type) => _selectedUnits
      .fold<int>(0, (sum, unit) => sum + unit.countOf(type));

  /// 所选单元 × 所选类型的合计条数（底部栏「共 X 条」）。
  int get _totalX =>
      _selectedTypes.fold<int>(0, (sum, t) => sum + _typeCount(t));

  bool get _canStart =>
      _selectedUnitIds.isNotEmpty && _selectedTypes.isNotEmpty && _totalX > 0;

  void _toggleUnit(int unitId) {
    setState(() {
      if (!_selectedUnitIds.remove(unitId)) {
        _selectedUnitIds.add(unitId);
      }
    });
  }

  /// 一级行勾选 = 全选 / 全不选其下二级（F15-1）。
  void _toggleGroup(UnitInfo group) {
    final leaves = _leafUnitsOf(group);
    final allSelected = leaves.every((u) => _selectedUnitIds.contains(u.id));
    setState(() {
      if (allSelected) {
        _selectedUnitIds.removeAll(leaves.map((u) => u.id));
      } else {
        _selectedUnitIds.addAll(leaves.map((u) => u.id));
      }
    });
  }

  void _toggleType(String type) {
    setState(() {
      if (!_selectedTypes.remove(type)) {
        _selectedTypes.add(type);
      }
    });
  }

  PracticeSelection _selection() {
    final units = _selectedUnits;
    return PracticeSelection(
      unitIds: units.map((u) => u.id).toList(),
      unitNames: units.map((u) => u.name).toList(),
      types: _selectedTypes.toList(),
      unitName: units.map((u) => u.name).join('、'),
    );
  }

  void _onRecite() {
    if (!_canStart) {
      return;
    }
    Navigator.of(context).pushNamed(
      ReciteRoutes.spellConfig,
      arguments: _selection(),
    );
  }

  /// 【听】进听写链路：复用 `practice/start`（mode=listen）多选组卷，
  /// 题目映射成 [DictationItem] 后进听写设置页；结果页结算完指回本页。
  bool _listenBusy = false;

  Future<void> _onListen() async {
    if (!_canStart || _listenBusy) {
      return;
    }
    setState(() => _listenBusy = true);
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      final run = await startSpellRun(
        ref.read(apiClientProvider),
        selection: _selection(),
        mode: 'listen',
      );
      if (!mounted) {
        return;
      }
      Navigator.of(context).pushNamed(
        DictationRoutes.settings,
        arguments: DictationSessionArgs(
          items: [
            for (final it in run.items)
              DictationItem(id: it.id, en: it.en, zh: it.zh, pos: it.pos),
          ],
          unitName: run.unitName,
          returnRoute: HomeRoutes.unitList,
        ),
      );
    } on ApiException catch (e) {
      messenger?.showSnackBar(
        SnackBar(
          content: Text(e.displayText),
          behavior: SnackBarBehavior.floating,
        ),
      );
    } on Object {
      messenger?.showSnackBar(
        const SnackBar(
          content: Text('没能开始这次听写，请再试一次'),
          behavior: SnackBarBehavior.floating,
        ),
      );
    } finally {
      if (mounted) {
        setState(() => _listenBusy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final units = _units;
    return Scaffold(
      appBar: AppBar(title: const Text('听背')),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: _buildList(units),
            ),
            _buildBottomBar(),
          ],
        ),
      ),
    );
  }

  Widget _buildList(List<UnitInfo>? units) {
    if (_error != null) {
      final error = _error;
      return ErrorView(
        message: error is ApiException
            ? error.displayText
            : '网络不太顺，没能拿到单元列表',
        onRetry: () => _load(),
      );
    }
    if (units == null) {
      return const LoadingView(message: '正在拿单元列表…');
    }
    if (units.isEmpty) {
      return const EmptyView(
        title: '还没有可练的单元',
        hint: '让管理者先在后台录入单元和内容。',
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
      itemCount: units.length,
      separatorBuilder: (_, _) => const SizedBox(height: 12),
      itemBuilder: (context, index) {
        final unit = units[index];
        if (!unit.isGroup) {
          return _unitRow(unit);
        }
        final expanded = _expanded.contains(unit.id);
        return Column(
          children: [
            _groupRow(unit, expanded),
            if (expanded) ...[
              for (final child in unit.children)
                Padding(
                  padding: const EdgeInsets.only(left: 20),
                  child: _unitRow(child),
                ),
            ],
          ],
        );
      },
    );
  }

  /// 一级分组行：点行体 = 展开/收起，点勾选框 = 全选/全不选其下二级。
  Widget _groupRow(UnitInfo group, bool expanded) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;
    final leaves = _leafUnitsOf(group);
    final selectedCount =
        leaves.where((u) => _selectedUnitIds.contains(u.id)).length;
    final allSelected = leaves.isNotEmpty && selectedCount == leaves.length;

    return Material(
      color: extra.card,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => setState(() {
          if (!_expanded.remove(group.id)) {
            _expanded.add(group.id);
          }
        }),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          child: Row(
            children: [
              Icon(
                expanded
                    ? Icons.expand_more_rounded
                    : Icons.chevron_right_rounded,
                color: extra.textSub,
              ),
              const SizedBox(width: 8),
              _checkIcon(
                allSelected
                    ? Icons.check_box_rounded
                    : selectedCount > 0
                        ? Icons.indeterminate_check_box_rounded
                        : Icons.check_box_outline_blank_rounded,
                color: allSelected || selectedCount > 0
                    ? palette.primary
                    : extra.textFaint,
                onTap: () => _toggleGroup(group),
                key: ValueKey('check-group-${group.id}'),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(group.name, style: profile.cardTitleStyle),
              ),
              Text(
                '${group.totalCount} 条',
                style: profile.captionStyle.copyWith(color: extra.textSub),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// 叶子单元行：整行点按 = 切换勾选。
  Widget _unitRow(UnitInfo unit) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;
    final selected = _selectedUnitIds.contains(unit.id);
    final total = unit.totalCount;

    return Material(
      color: selected ? palette.primary.withValues(alpha: 0.12) : extra.card,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => _toggleUnit(unit.id),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          child: Row(
            children: [
              _checkIcon(
                selected
                    ? Icons.check_box_rounded
                    : Icons.check_box_outline_blank_rounded,
                color: selected ? palette.primary : extra.textFaint,
                onTap: () => _toggleUnit(unit.id),
                key: ValueKey('check-unit-${unit.id}'),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  unit.name,
                  style: profile.bodyStyle.copyWith(
                    color: selected ? palette.primary : null,
                  ),
                ),
              ),
              Text(
                total > 0 ? '$total 条' : '暂无内容',
                style: profile.captionStyle.copyWith(
                  color: total > 0 ? extra.textSub : extra.textFaint,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _checkIcon(
    IconData icon, {
    required Color color,
    VoidCallback? onTap,
    Key? key,
  }) {
    return InkWell(
      key: key,
      borderRadius: BorderRadius.circular(6),
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.all(4),
        child: Icon(icon, size: 24, color: color),
      ),
    );
  }

  Widget _buildBottomBar() {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final unitCount = _selectedUnitIds.length;

    final String statusText;
    if (unitCount == 0) {
      statusText = '先在上面勾选要练的单元';
    } else if (_selectedTypes.isEmpty) {
      statusText = '至少选一种类型（单词 / 词组 / 短句）';
    } else if (_totalX == 0) {
      statusText = '所选单元里还没有这些类型的内容';
    } else {
      statusText = '已选 $unitCount 个单元 · 共 $_totalX 条';
    }

    return Container(
      decoration: BoxDecoration(
        color: extra.card,
        border: Border(top: BorderSide(color: extra.divider, width: 0.5)),
      ),
      padding: const EdgeInsets.fromLTRB(
        AgeProfile.screenEdgeInset,
        12,
        AgeProfile.screenEdgeInset,
        12,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              for (final entry in _typeLabels.entries) ...[
                if (entry.key != _typeLabels.keys.first)
                  const SizedBox(width: 12),
                Expanded(child: _typeCheckbox(entry.key, entry.value)),
              ],
            ],
          ),
          const SizedBox(height: AgeProfile.adjacentButtonGap),
          Row(
            children: [
              Expanded(
                child: AppButton(
                  label: '听',
                  icon: Icons.headphones_rounded,
                  kind: AppButtonKind.secondary,
                  loading: _listenBusy,
                  onPressed: _canStart ? _onListen : null,
                ),
              ),
              const SizedBox(width: AgeProfile.adjacentButtonGap),
              Expanded(
                child: AppButton(
                  label: '背',
                  icon: Icons.edit_note_rounded,
                  onPressed: _canStart ? _onRecite : null,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            statusText,
            style: profile.captionStyle.copyWith(
              color: _canStart ? extra.textSub : extra.textFaint,
            ),
          ),
        ],
      ),
    );
  }

  /// 类型 checkbox（F15-3：默认全不选，至少选一；条数按所选单元求和）。
  Widget _typeCheckbox(String type, String label) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;
    final selected = _selectedTypes.contains(type);
    final count = _typeCount(type);

    return Semantics(
      button: true,
      enabled: true,
      label: label,
      child: InkWell(
        key: ValueKey('check-type-$type'),
        borderRadius: BorderRadius.circular(12),
        onTap: () => _toggleType(type),
        child: Container(
          height: AgeProfile.minTouchTarget,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: selected ? palette.primary : extra.divider,
              width: selected ? 1.5 : 0.5,
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                selected
                    ? Icons.check_box_rounded
                    : Icons.check_box_outline_blank_rounded,
                size: 20,
                color: selected ? palette.primary : extra.textSub,
              ),
              const SizedBox(width: 6),
              Flexible(
                child: Text(
                  _selectedUnitIds.isEmpty ? label : '$label $count',
                  overflow: TextOverflow.ellipsis,
                  style: profile.bodyStyle,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
