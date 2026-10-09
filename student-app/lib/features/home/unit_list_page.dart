/// 听背 · 选单元页 —— 简化版 WP-C（功能点 2/3/4）。
///
/// 流程：`GET /api/units` 拉全量单元树 → 点选单元 → 类型单选
/// （○单词 ○词组 ○短句，本地按 counts 置零空类型）→ 底部【听】【背】。
/// 【听】跳听写是 WP-D 的页面（接力项 J1，未交付前置灰，本文件不 import D）；
/// 【背】进背诵模式选择（`spell_config_page`）。
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

/// `/api/units` 里一个单元（契约 §4.1：`{id,name,sort,counts:{...}}`）。
class UnitInfo {
  const UnitInfo({
    required this.id,
    required this.name,
    required this.sort,
    required this.counts,
  });

  factory UnitInfo.fromJson(Map<String, dynamic> json) => UnitInfo(
        id: (json['id'] as num?)?.toInt() ?? 0,
        name: json['name']?.toString() ?? '',
        sort: (json['sort'] as num?)?.toInt() ?? 0,
        counts: <String, int>{
          'word': _countOf(json, 'word'),
          'phrase': _countOf(json, 'phrase'),
          'sentence': _countOf(json, 'sentence'),
        },
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

  /// `word` / `phrase` / `sentence` → 条数。
  final Map<String, int> counts;

  int countOf(String type) => counts[type] ?? 0;
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
  int? _selectedUnitId;
  String _type = 'word';

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
        // 默认选中第一个还有内容的单元
        _selectedUnitId ??= units
            .firstWhere(
              (u) => u.counts.values.any((c) => c > 0),
              orElse: () => units.first,
            )
            .id;
      });
    } on Object catch (e) {
      if (mounted) {
        setState(() => _error = e);
      }
    }
  }

  UnitInfo? get _unit =>
      _units?.where((u) => u.id == _selectedUnitId).firstOrNull;

  void _onRecite() {
    final unit = _unit;
    if (unit == null) {
      return;
    }
    Navigator.of(context).pushNamed(
      ReciteRoutes.spellConfig,
      arguments: PracticeSelection(
        unitId: unit.id,
        unitName: unit.name,
        type: _type,
      ),
    );
  }

  /// 【听】进听写链路（J2' 销账：WP-D 已交付，2026-10-09 集成接线）。
  ///
  /// 复用 `practice/start`（mode=listen）组卷，把题目映射成
  /// [DictationItem] 后进听写设置页；`returnRoute` 指回本页，
  /// 结果页结算完 `pushNamedAndRemoveUntil` 回这里。
  bool _listenBusy = false;

  Future<void> _onListen() async {
    final unit = _unit;
    if (unit == null || _listenBusy) {
      return;
    }
    setState(() => _listenBusy = true);
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      final run = await startSpellRun(
        ref.read(apiClientProvider),
        selection: PracticeSelection(
          unitId: unit.id,
          unitName: unit.name,
          type: _type,
        ),
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
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;
    final selectedId = _selectedUnitId;

    return ListView.separated(
      padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
      itemCount: units.length,
      separatorBuilder: (_, _) => const SizedBox(height: 12),
      itemBuilder: (context, index) {
        final unit = units[index];
        final selected = unit.id == selectedId;
        final total = unit.counts.values.fold<int>(0, (a, b) => a + b);
        return Material(
          color: selected ? palette.primary.withValues(alpha: 0.12) : extra.card,
          borderRadius: BorderRadius.circular(12),
          child: InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: () => setState(() => _selectedUnitId = unit.id),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
              child: Row(
                children: [
                  Icon(
                    selected
                        ? Icons.check_circle_rounded
                        : Icons.radio_button_unchecked_rounded,
                    color: selected ? palette.primary : extra.textFaint,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      unit.name,
                      style: profile.cardTitleStyle.copyWith(
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
      },
    );
  }

  Widget _buildBottomBar() {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final unit = _unit;
    final reciteEnabled = unit != null && unit.countOf(_type) > 0;

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
                Expanded(child: _typeRadio(entry.key, entry.value)),
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
                  // J2' 销账：听写链路已就绪（2026-10-09 集成接线）。
                  onPressed: reciteEnabled ? _onListen : null,
                ),
              ),
              const SizedBox(width: AgeProfile.adjacentButtonGap),
              Expanded(
                child: AppButton(
                  label: '背',
                  icon: Icons.edit_note_rounded,
                  onPressed: reciteEnabled ? _onRecite : null,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            reciteEnabled
                ? '开始${_typeLabels[_type]}背诵'
                : unit != null
                    ? '这个单元还没有${_typeLabels[_type]}'
                    : '先在上面选一个单元',
            style: profile.captionStyle.copyWith(color: extra.textFaint),
          ),
        ],
      ),
    );
  }

  Widget _typeRadio(String type, String label) {
    final unit = _unit;
    final count = unit?.countOf(type) ?? 0;
    final enabled = count > 0;
    final profile = AgeScope.of(context);
    final palette = Theme.of(context).colorScheme;
    final extra = AppExtraColors.of(context);

    return Semantics(
      button: true,
      enabled: enabled,
      label: label,
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: enabled ? () => setState(() => _type = type) : null,
        child: Container(
          height: AgeProfile.minTouchTarget,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: _type == type ? palette.primary : extra.divider,
              width: _type == type ? 1.5 : 0.5,
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                _type == type
                    ? Icons.radio_button_checked_rounded
                    : Icons.radio_button_unchecked_rounded,
                size: 20,
                color: enabled
                    ? (_type == type ? palette.primary : extra.textSub)
                    : extra.textFaint,
              ),
              const SizedBox(width: 6),
              Flexible(
                child: Text(
                  count > 0 ? '$label $count' : label,
                  overflow: TextOverflow.ellipsis,
                  style: profile.bodyStyle.copyWith(
                    color: enabled ? null : extra.textFaint,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
