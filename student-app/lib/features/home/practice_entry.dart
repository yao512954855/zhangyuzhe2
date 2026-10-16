/// 练习入口与模式分发 —— 简化版 WP-C（功能点 2/4）。
///
/// 单元列表页【背】→ 这里带着 `PracticeSelection` 进 `spell_config_page`
/// 选模式 → `launchSpellRun` 发 `POST /api/practice/start`
/// （契约 §4.1 v1.3：多选 `{mode,unit_ids,types}`；单数 `{unit_id,type,mode}`
/// 保留兼容错题本重做等入口，返回 `{session_id, items:[...]}`）
/// → 进答题页。
library;

import 'package:flutter/material.dart';

import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../router/routes/recite.dart';
import '../recite/spell_session.dart';

/// 一次听背选择（单元 + 类型）。
///
/// v1.3（todo11 A1/A2）起支持多单元多类型（契约 §4.1）：
/// - 选单元页等多选入口传 [unitIds]/[unitNames]/[types]；
/// - 错题本重做等**单单元入口**保留 [unitId]/[unitName]/[type] 单数字段
///   （契约：单数保留兼容；同请求两者都传时以数组为准）。
class PracticeSelection {
  const PracticeSelection({
    this.unitId,
    this.unitName = '',
    this.type,
    this.unitIds = const <int>[],
    this.unitNames = const <String>[],
    this.types = const <String>[],
  });

  /// 单单元入口：单元 id（错题本重做等）。
  final int? unitId;

  /// 单单元入口：单元名（展示用）。
  final String unitName;

  /// 单单元入口：`word` / `phrase` / `sentence`（§7 枚举，禁止第二套命名）。
  final String? type;

  /// 多选入口：单元 id 列表，**顺序 = 勾选顺序**（组卷按单元顺序，契约 §4.1）。
  final List<int> unitIds;

  /// 多选入口：单元名列表（与 [unitIds] 同序，展示用）。
  final List<String> unitNames;

  /// 多选入口：类型列表（§7 枚举；顺序 = 勾选顺序）。
  final List<String> types;

  /// 是否多选入口（unitIds 非空 = 发数组请求体）。
  bool get isMulti => unitIds.isNotEmpty;

  /// 展示用单元名（多选 = 顿号连接）。
  String get displayUnitName => isMulti ? unitNames.join('、') : unitName;

  /// 展示用类型名（多选 = 中文标签顿号连接；单选入口保留原值不动）。
  String get displayTypeName {
    if (!isMulti) {
      return type ?? '';
    }
    return [
      for (final t in types) itemTypeLabels[t] ?? t,
    ].join('、');
  }
}

/// §7 item_type 的中文标签（三端 UI 统一用词）。
const Map<String, String> itemTypeLabels = <String, String>{
  'word': '单词',
  'phrase': '词组',
  'sentence': '短句',
};

int _toInt(Object? v) {
  if (v is int) {
    return v;
  }
  if (v is num) {
    return v.toInt();
  }
  return int.tryParse(v?.toString() ?? '') ?? 0;
}

/// 发 `POST /api/practice/start` 并解析成 [SpellRun]。
///
/// 单测断请求体用（U3/U15-4）：抛出前把 body 组装逻辑独立成 [spellStartBody]。
Map<String, dynamic> spellStartBody(PracticeSelection selection, String mode) {
  // v1.3 多选（契约 §4.1）：{mode, unit_ids:[...], types:[...]}，
  // 数组顺序 = 勾选顺序；只勾 1 个也发数组（单元素，不发单数字段）。
  if (selection.isMulti) {
    return <String, dynamic>{
      'mode': mode,
      'unit_ids': List<int>.of(selection.unitIds),
      'types': List<String>.of(selection.types),
    };
  }
  // 单数兼容（契约 §4.1：错题本重做等单单元入口保留单数字段）。
  return <String, dynamic>{
    'unit_id': selection.unitId,
    'type': selection.type,
    'mode': mode,
  };
}

Future<SpellRun> startSpellRun(
  ApiClient api, {
  required PracticeSelection selection,
  required String mode,
}) async {
  final data = await api.post<Map<String, dynamic>>(
    '/api/practice/start',
    body: spellStartBody(selection, mode),
  );
  if (data == null) {
    throw ApiException.parse(null);
  }
  final sessionId = data['session_id']?.toString() ?? '';
  if (sessionId.isEmpty) {
    // 契约约定 start 返回 session_id 字符串；缺失按解析失败处理（不假装成功）。
    throw ApiException.parse(data);
  }
  final rawItems = data['items'];
  final items = <SpellItem>[];
  if (rawItems is List) {
    for (final raw in rawItems) {
      if (raw is Map) {
        final m = Map<String, dynamic>.from(raw);
        items.add(
          SpellItem(
            id: _toInt(m['id']),
            en: m['en']?.toString() ?? '',
            pos: m['pos']?.toString() ?? '',
            zh: m['zh']?.toString() ?? '',
            altZh: m['alt_zh']?.toString() ?? '',
          ),
        );
      }
    }
  }
  if (items.isEmpty) {
    // 空组卷：后端本应回 20004；防御一层，不进一个答不了的页面。
    throw ApiException.parse(data);
  }
  return SpellRun(
    sessionId: sessionId,
    mode: mode,
    unitName: selection.displayUnitName,
    typeName: selection.displayTypeName,
    items: items,
    startedAt: DateTime.now(),
    // v1.3：带出本轮快照（settle 透传用，F16-7 接线在 APP-12）。
    unitIds: _snapshotUnitIds(selection),
    types: _snapshotTypes(selection),
  );
}

List<int> _snapshotUnitIds(PracticeSelection selection) {
  if (selection.isMulti) {
    return List<int>.of(selection.unitIds);
  }
  final id = selection.unitId;
  return id == null ? const <int>[] : <int>[id];
}

List<String> _snapshotTypes(PracticeSelection selection) {
  if (selection.isMulti) {
    return List<String>.of(selection.types);
  }
  final type = selection.type;
  return type == null ? const <String>[] : <String>[type];
}

/// 组卷并进答题页。返回是否成功进入（失败时已上屏中文提示）。
///
/// 调用方按钮自带 `_busy` + finally（§9 学生端规范）；
/// 本函数只负责发请求、解析、导航与错误上屏。
Future<bool> launchSpellRun(
  BuildContext context, {
  required ApiClient api,
  required PracticeSelection selection,
  required String mode,
}) async {
  final messenger = ScaffoldMessenger.maybeOf(context);
  try {
    final run = await startSpellRun(api, selection: selection, mode: mode);
    if (!context.mounted) {
      return false;
    }
    await Navigator.of(context).pushReplacementNamed(
      ReciteRoutes.spell,
      arguments: run,
    );
    return true;
  } on ApiException catch (e) {
    // 20004 无可练内容等业务错误：直接上屏服务端中文消息（U2）。
    messenger?.showSnackBar(
      SnackBar(
        content: Text(e.displayText),
        behavior: SnackBarBehavior.floating,
      ),
    );
  } on Object {
    messenger?.showSnackBar(
      const SnackBar(
        content: Text('没能开始这次背诵，请再试一次'),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }
  return false;
}
