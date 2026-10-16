/// 错题本页 —— 简化版 WP-E + APP-13 F17-4/5/6（契约 v1.3，todo11 A7）。
///
/// **三类 tab**（F17-4）：听写 / 看中背英 / 看英背中，按 `mode` 过滤
/// （`GET /api/wrongbook?mode=&status=&group_by=unit&page&page_size`）；
/// 每 tab 内仍按 `unit_name` 连续分组展示；分页加载（滚到底部预取）。
///
/// **状态过滤**（F17-5）：status 默认 `unmastered`，可切 `mastered`/`all`；
/// `mastered_at` 非空的条目带「已掌握」小标记（判对保留显示）。
///
/// **删除**（F17-6）：单条 = 长按或卡片上的删除按钮（确认弹窗 →
/// `DELETE /api/wrongbook/{item_id}/{mode}`）；批量 = 进多选模式勾选 →
/// `POST /api/wrongbook/batch-resolve`（body `{entries:[{item_id,mode}]}`）
/// → 返回 `{resolved:n}` → 列表刷新。
///
/// 重做按 [groupForRedo] 分组，多组时先选组再进原链路。
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
import 'wrongbook_entry.dart';
import 'wrongbook_models.dart';

/// 三类 tab 的顺序与短标题（契约 §7 mode 枚举）。
const _tabModes = <String>['listen', 'spell_zh2en', 'spell_en2zh'];
const _tabTitles = <String>['听写', '看中背英', '看英背中'];

/// 错题本列表页。
class WrongbookPage extends ConsumerStatefulWidget {
  const WrongbookPage({super.key});

  @override
  ConsumerState<WrongbookPage> createState() => _WrongbookPageState();
}

class _WrongbookPageState extends ConsumerState<WrongbookPage>
    with SingleTickerProviderStateMixin {
  static const _pageSize = 20;

  late final TabController _tab =
      TabController(length: _tabModes.length, vsync: this)..addListener(_onTabChanged);

  String get _mode => _tabModes[_tab.index];

  /// status 过滤（契约 §4.1：unmastered 默认 | mastered | all）。
  String _status = 'unmastered';

  List<WrongItem> _items = <WrongItem>[];
  int _total = 0;
  int _nextPage = 1;

  bool _loading = true; // 首屏 / 切 tab / 切状态
  bool _loadingMore = false;
  bool _hasMore = false;
  Object? _error;

  /// 异步按钮防重（契约 §9：移出 / 批量 / 重做共用一把）。
  bool _busy = false;

  /// 批量多选（F17-6）。
  bool _selectionMode = false;
  final Set<String> _selected = <String>{};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _reload());
  }

  @override
  void dispose() {
    _tab.removeListener(_onTabChanged);
    _tab.dispose();
    super.dispose();
  }

  void _onTabChanged() {
    if (_tab.indexIsChanging) {
      return; // 只响应动画结束后的最终 index
    }
    _exitSelection();
    _reload();
  }

  // ---------------------------------------------------------------- 数据

  Future<void> _reload() async {
    setState(() {
      _error = null;
      _loading = true;
    });
    try {
      final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
            '/api/wrongbook',
            query: <String, dynamic>{
              'group_by': 'unit',
              'mode': _mode,
              'status': _status,
              'page': 1,
              'page_size': _pageSize,
            },
          );
      final page = _parsePage(data);
      if (!mounted) {
        return;
      }
      setState(() {
        _items = page.items;
        _total = page.total;
        _nextPage = 2;
        _hasMore = _items.length < _total;
        _loading = false;
      });
    } on Object catch (e) {
      if (mounted) {
        setState(() {
          _error = e;
          _loading = false;
        });
      }
    }
  }

  Future<void> _loadMore() async {
    if (_loadingMore || !_hasMore || _error != null) {
      return;
    }
    setState(() => _loadingMore = true);
    try {
      final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
            '/api/wrongbook',
            query: <String, dynamic>{
              'group_by': 'unit',
              'mode': _mode,
              'status': _status,
              'page': _nextPage,
              'page_size': _pageSize,
            },
          );
      final page = _parsePage(data);
      if (!mounted) {
        return;
      }
      setState(() {
        // 分页无重复无遗漏（U2）：按 (item_id, mode) 去重兜一层。
        final seen = _items.map((it) => it.key).toSet();
        for (final it in page.items) {
          if (seen.add(it.key)) {
            _items.add(it);
          }
        }
        _nextPage += 1;
        _hasMore = _items.length < _total;
      });
    } on Object {
      // 加载下一页失败不掀桌：保留已有内容，滚回来会再试。
    } finally {
      if (mounted) {
        setState(() => _loadingMore = false);
      }
    }
  }

  /// 分页信封 `{items, total, page, page_size}`（契约 §5.3）。
  static _WrongbookPage _parsePage(Map<String, dynamic>? data) {
    final rawItems = data?['items'];
    final items = <WrongItem>[
      if (rawItems is List)
        for (final raw in rawItems)
          if (raw is Map) WrongItem.fromJson(Map<String, dynamic>.from(raw)),
    ];
    return _WrongbookPage(
      items: items,
      total: (data?['total'] as num?)?.toInt() ?? items.length,
    );
  }

  // ---------------------------------------------------------------- 单条移出

  Future<void> _remove(WrongItem item) async {
    if (_busy || _selectionMode) {
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('移出错题本'),
        content: Text(
          '把「${item.en}」从错题本移出？${modeLabels[item.mode] ?? item.mode}'
          '移出后它不会再出现在这里。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('先留着'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('移出'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted || _busy) {
      return;
    }
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      await ref.read(apiClientProvider).delete<void>(
            '/api/wrongbook/${item.itemId}/${item.mode}',
          );
      if (!mounted) {
        return;
      }
      setState(() {
        _items.removeWhere((it) => it.key == item.key);
        _total = (_total - 1).clamp(0, 1 << 31);
        _hasMore = _items.length < _total;
      });
      messenger?.showSnackBar(
        SnackBar(
          content: Text('「${item.en}」已移出错题本'),
          behavior: SnackBarBehavior.floating,
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
          content: Text('没能移出，请再试一次'),
          behavior: SnackBarBehavior.floating,
        ),
      );
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  // ---------------------------------------------------------------- 批量移出

  void _enterSelection() {
    if (_items.isEmpty) {
      return;
    }
    setState(() {
      _selectionMode = true;
      _selected.clear();
    });
  }

  void _exitSelection() {
    if (!_selectionMode && _selected.isEmpty) {
      return;
    }
    setState(() {
      _selectionMode = false;
      _selected.clear();
    });
  }

  void _toggleSelect(WrongItem item) {
    setState(() {
      if (!_selected.add(item.key)) {
        _selected.remove(item.key);
      }
    });
  }

  Future<void> _batchResolve() async {
    if (_busy || _selected.isEmpty) {
      return;
    }
    final count = _selected.length;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('移出错题本'),
        content: Text('把选中的 $count 条错题一次移出？移出后它们不会再出现在这里。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('先留着'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('移出'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted || _busy) {
      return;
    }
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      final entries = <Map<String, dynamic>>[
        for (final it in _items)
          if (_selected.contains(it.key))
            {'item_id': it.itemId, 'mode': it.mode},
      ];
      final data = await ref.read(apiClientProvider).post<Map<String, dynamic>>(
            '/api/wrongbook/batch-resolve',
            body: {'entries': entries},
          );
      final resolved = (data?['resolved'] as num?)?.toInt() ?? entries.length;
      if (!mounted) {
        return;
      }
      _exitSelection();
      await _reload();
      messenger?.showSnackBar(
        SnackBar(
          content: Text('已移出 $resolved 条错题'),
          behavior: SnackBarBehavior.floating,
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
          content: Text('没能移出，请再试一次'),
          behavior: SnackBarBehavior.floating,
        ),
      );
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  // ---------------------------------------------------------------- 重做

  Future<void> _onRedo() async {
    if (_busy || _items.isEmpty) {
      return;
    }
    final groups = groupForRedo(_items);
    var group = groups.length == 1 ? groups.first : null;
    if (group == null) {
      // 多组：先选从哪组开始（每走完一组回到这里，可继续下一组）。
      group = await _pickGroup(groups);
      if (group == null || !mounted) {
        return;
      }
    }
    setState(() => _busy = true);
    try {
      await launchRedoGroup(context, api: ref.read(apiClientProvider), group: group);
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<RedoGroup?> _pickGroup(List<RedoGroup> groups) {
    return showModalBottomSheet<RedoGroup>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
              child: Text(
                '这些错题分了几种练法，先重做哪一组？',
                style: AgeScope.of(sheetContext).cardTitleStyle,
              ),
            ),
            for (final group in groups)
              ListTile(
                leading: Icon(_modeIcon(group.mode)),
                title: Text('${group.label}（${group.count} 题）'),
                subtitle: group.type.isEmpty
                    ? Text(group.unitName)
                    : Text('${group.unitName} · ${group.type}'),
                onTap: () => Navigator.of(sheetContext).pop(group),
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  // ---------------------------------------------------------------- 渲染

  IconData _modeIcon(String mode) => switch (mode) {
        'listen' => Icons.headphones_rounded,
        'spell_zh2en' => Icons.edit_note_rounded,
        _ => Icons.record_voice_over_rounded,
      };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('错题本'),
        actions: [
          if (_selectionMode)
            TextButton(
              onPressed: _exitSelection,
              child: const Text('取消'),
            )
          else
            TextButton.icon(
              onPressed: _items.isEmpty ? null : _enterSelection,
              icon: const Icon(Icons.checklist_rounded, size: 20),
              label: const Text('批量删除'),
            ),
        ],
        bottom: TabBar(
          controller: _tab,
          tabs: [for (final title in _tabTitles) Tab(text: title)],
        ),
      ),
      body: SafeArea(
        child: _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    if (_error != null) {
      final error = _error;
      return Column(
        children: [
          _statusFilter(),
          Expanded(
            child: ErrorView(
              message: error is ApiException
                  ? error.displayText
                  : '网络不太顺，没能拿到错题本',
              onRetry: () => _reload(),
            ),
          ),
        ],
      );
    }
    if (_loading) {
      return Column(
        children: [
          _statusFilter(),
          const Expanded(child: LoadingView(message: '正在拿错题本…')),
        ],
      );
    }
    if (_items.isEmpty) {
      // U6：空错题本空态，文案友好。
      return Column(
        children: [
          _statusFilter(),
          const Expanded(
            child: EmptyView(
              title: '这个分类还没有错题',
              hint: '先去【听背】练一练，做错的题会自动收进来。',
            ),
          ),
        ],
      );
    }
    return Column(
      children: [
        _statusFilter(),
        Expanded(
          child: NotificationListener<Notification>(
            onNotification: _onScroll,
            child: ListView(
              padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
              children: _buildSections(),
            ),
          ),
        ),
        _buildBottomBar(),
      ],
    );
  }

  /// status 过滤（F17-5）：unmastered 默认 | mastered | all。
  Widget _statusFilter() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AgeProfile.screenEdgeInset,
        8,
        AgeProfile.screenEdgeInset,
        0,
      ),
      child: SegmentedButton<String>(
        segments: [
          for (final entry in statusLabels.entries)
            ButtonSegment(value: entry.key, label: Text(entry.value)),
        ],
        selected: {_status},
        onSelectionChanged: (selection) {
          if (selection.first == _status) {
            return;
          }
          setState(() {
            _status = selection.first;
            _selected.clear();
          });
          _reload();
        },
      ),
    );
  }

  /// 滚动到底部（或内容不满一屏）时预取下一页。
  ///
  /// ⚠️ 只听 `ScrollNotification` 会漏掉「内容不足一屏」的场景——
  /// 列表根本滚不动、永远没有滚动事件，第 2 页就永远出不来；
  /// `ScrollMetricsNotification` 在布局/内容变化时也会派发，正好补上。
  bool _onScroll(Notification notification) {
    final metrics = notification is ScrollMetricsNotification
        ? notification.metrics
        : notification is ScrollNotification
            ? notification.metrics
            : null;
    if (metrics != null && metrics.extentAfter < 300) {
      _loadMore();
    }
    return false;
  }

  /// 按 `unit_name` 连续分段（后端 `group_by=unit` 已按单元排序）。
  List<Widget> _buildSections() {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final widgets = <Widget>[];
    var index = 0;
    while (index < _items.length) {
      final unitName = _items[index].unitName;
      var end = index;
      while (end < _items.length && _items[end].unitName == unitName) {
        end += 1;
      }
      final count = end - index;
      widgets
        ..add(Padding(
          padding: EdgeInsets.only(top: index == 0 ? 0 : 8, bottom: 8),
          child: Text(
            '$unitName · $count 题',
            style: profile.cardTitleStyle.copyWith(color: extra.textSub),
          ),
        ))
        ..add(const SizedBox(height: 8));
      for (var i = index; i < end; i++) {
        widgets.add(_itemCard(_items[i]));
        widgets.add(const SizedBox(height: 12));
      }
      index = end;
    }
    if (_loadingMore) {
      widgets.add(const Padding(
        padding: EdgeInsets.symmetric(vertical: 12),
        child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
      ));
    }
    return widgets;
  }

  Widget _itemCard(WrongItem item) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final lastDay = formatRelativeDay(item.lastWrongAt);
    final selected = _selected.contains(item.key);

    return GestureDetector(
      onTap: _selectionMode ? () => _toggleSelect(item) : null,
      onLongPress: _selectionMode ? null : () => _remove(item),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        decoration: BoxDecoration(
          color: selected ? extra.hintLight : extra.card,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: selected ? extra.hint : extra.divider,
            width: selected ? 1 : 0.5,
          ),
        ),
        child: Row(
          children: [
            if (_selectionMode) ...[
              Checkbox(value: selected, onChanged: (_) => _toggleSelect(item)),
              const SizedBox(width: 4),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Flexible(
                        child: Text(
                          item.en,
                          overflow: TextOverflow.ellipsis,
                          style: profile.cardTitleStyle,
                        ),
                      ),
                      const SizedBox(width: 8),
                      _modeBadge(item.mode),
                      // F17-5：已掌握小标记（mastered_at 非空，判对保留显示）。
                      if (item.isMastered) ...[
                        const SizedBox(width: 6),
                        _masteredBadge(),
                      ],
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(
                    item.pos.isEmpty ? item.zh : '${item.pos} ${item.zh}',
                    style: profile.bodyStyle.copyWith(color: extra.textSub),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    lastDay.isEmpty
                        ? '错 ${item.wrongCount} 次'
                        : '错 ${item.wrongCount} 次 · $lastDay',
                    style: profile.captionStyle.copyWith(color: extra.textFaint),
                  ),
                ],
              ),
            ),
            if (!_selectionMode)
              IconButton(
                tooltip: '移出错题本',
                icon: Icon(
                  Icons.delete_outline_rounded,
                  size: profile.safeIconSize / 1.4,
                  color: extra.textFaint,
                ),
                onPressed: _busy ? null : () => _remove(item),
              ),
          ],
        ),
      ),
    );
  }

  /// 「已掌握」徽标（F17-5）。
  Widget _masteredBadge() {
    final profile = AgeScope.of(context);
    final palette = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: palette.secondaryContainer,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        '已掌握',
        style: profile.captionStyle.copyWith(color: palette.onSecondaryContainer),
      ),
    );
  }

  /// mode 徽标：只用现有主题 token（hint / primaryContainer / card）。
  Widget _modeBadge(String mode) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;

    final (Color fg, Color bg) = switch (mode) {
      'listen' => (extra.hint, extra.hintLight),
      'spell_zh2en' => (palette.primary, palette.primaryContainer),
      _ => (extra.textSub, extra.card),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: fg.withValues(alpha: 0.35), width: 0.5),
      ),
      child: Text(
        modeLabels[mode] ?? mode,
        style: profile.captionStyle.copyWith(color: fg),
      ),
    );
  }

  Widget _buildBottomBar() {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    if (_selectionMode) {
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
        child: Row(
          children: [
            Expanded(
              child: AppButton(
                label: _selected.isEmpty
                    ? '移出所选'
                    : '移出所选（${_selected.length}）',
                loading: _busy,
                onPressed: _selected.isEmpty ? null : _batchResolve,
              ),
            ),
          ],
        ),
      );
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
          AppButton(
            label: '重做全部（${_items.length} 题）',
            icon: Icons.refresh_rounded,
            loading: _busy,
            onPressed: _items.isEmpty ? null : _onRedo,
          ),
          const SizedBox(height: 8),
          Text(
            '判对的题会标记「已掌握」，可反复巩固；只有手动删除才会移出',
            style: profile.captionStyle.copyWith(color: extra.textFaint),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}

/// 一页错题（分页信封的 data 部分）。
class _WrongbookPage {
  const _WrongbookPage({required this.items, required this.total});

  final List<WrongItem> items;
  final int total;
}
