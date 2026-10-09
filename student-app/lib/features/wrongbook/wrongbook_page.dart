/// 错题本页 —— 简化版 WP-E（功能点 1/2/4 + 重做入口，验收 U1-U6）。
///
/// `GET /api/wrongbook?group_by=unit&page&page_size` 平铺列表（后端已按
/// 单元排序），这里按 `unit_name` 连续分组展示；分页加载（滚到底部预取）。
/// 移出 = `DELETE /api/wrongbook/{item_id}/{mode}`，需确认弹窗；
/// 重做按 [groupForRedo] 分组，多组时先选组再进原链路。
///
/// 入口：首页【错题本】按钮（接力项 J2，`home_page.dart:46`
/// `_HomeEntry(enabled:false)` 改 `enabled:true` 并跳 [WrongbookRoutes.wrongbook]）。
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
import '../../router/routes/wrongbook.dart';
import 'wrongbook_entry.dart';
import 'wrongbook_models.dart';

/// 错题本列表页。
class WrongbookPage extends ConsumerStatefulWidget {
  const WrongbookPage({super.key});

  @override
  ConsumerState<WrongbookPage> createState() => _WrongbookPageState();
}

class _WrongbookPageState extends ConsumerState<WrongbookPage> {
  static const _pageSize = 20;

  List<WrongItem> _items = <WrongItem>[];
  int _total = 0;
  int _nextPage = 1;

  bool _loading = true; // 首屏
  bool _loadingMore = false;
  bool _hasMore = false;
  Object? _error;

  /// 异步按钮防重（契约 §9：移出 / 重做共用一把）。
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _reload());
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
            query: <String, dynamic>{'group_by': 'unit', 'page': 1, 'page_size': _pageSize},
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

  // ---------------------------------------------------------------- 移出

  Future<void> _remove(WrongItem item) async {
    if (_busy) {
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('移出错题本'),
        content: Text(
          '把「${item.en}」从错题本移出？${modeLabels[item.mode] ?? item.mode}'
          '练对了它也不会再出现在这里。',
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
      appBar: AppBar(title: const Text('错题本')),
      body: SafeArea(
        child: _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    if (_error != null) {
      final error = _error;
      return ErrorView(
        message: error is ApiException
            ? error.displayText
            : '网络不太顺，没能拿到错题本',
        onRetry: () => _reload(),
      );
    }
    if (_loading) {
      return const LoadingView(message: '正在拿错题本…');
    }
    if (_items.isEmpty) {
      // U6：空错题本空态，文案友好。
      return const EmptyView(
        title: '错题本是空的',
        hint: '先去【听背】练一练，做错的题会自动收进来。',
      );
    }
    return Column(
      children: [
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

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      decoration: BoxDecoration(
        color: extra.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: extra.divider, width: 0.5),
      ),
      child: Row(
        children: [
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
    final hasListen = _items.any((it) => it.mode == 'listen');

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
            hasListen
                ? '听写题在纸上写完拍照批改；判对的题会自动移出错题本'
                : '判对的题会自动移出错题本',
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
