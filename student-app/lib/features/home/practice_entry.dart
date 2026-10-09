/// 练习入口与模式分发 —— 简化版 WP-C（功能点 2/4）。
///
/// 单元列表页【背】→ 这里带着 `PracticeSelection` 进 `spell_config_page`
/// 选模式 → `launchSpellRun` 发 `POST /api/practice/start`
/// （契约 §4.1：`{unit_id,type,mode}`，返回 `{session_id, items:[...]}`）
/// → 进答题页。
library;

import 'package:flutter/material.dart';

import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../router/routes/recite.dart';
import '../recite/spell_session.dart';

/// 一次听背选择（单元 + 类型）。
class PracticeSelection {
  const PracticeSelection({
    required this.unitId,
    required this.unitName,
    required this.type,
  });

  final int unitId;
  final String unitName;

  /// `word` / `phrase` / `sentence`（§7 枚举，禁止第二套命名）。
  final String type;
}

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
/// 单测断请求体用（U3）：抛出前把 body 组装逻辑独立成 [spellStartBody]。
Map<String, dynamic> spellStartBody(PracticeSelection selection, String mode) =>
    <String, dynamic>{
      'unit_id': selection.unitId,
      'type': selection.type,
      'mode': mode,
    };

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
    unitName: selection.unitName,
    typeName: selection.type,
    items: items,
    startedAt: DateTime.now(),
  );
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
