/// 重做组卷与分发 —— 简化版 WP-E（功能点 3，J3 接线在本文件内完成）。
///
/// - `listen` 组 → 跳听写设置页（WP-D 页面）：错题 item 直接构造
///   [DictationSessionArgs]（听写判分走 `/api/dictation/grade`，
///   不需要 practice 会话），`returnRoute` 指回错题本。
/// - `spell_zh2en` / `spell_en2zh` 组 → 先发 `POST /api/practice/start`
///   拿**真会话**（后端 `answer`/`settle` 校验 session_id，不能伪造），
///   再把返回的全量条目过滤成本组错题，构造 [SpellRun] 进背诵答题页
///   （[SpellPage] 的参数解析只要求 `args is SpellRun`）。
///
/// 判对移出（`DELETE /api/wrongbook/{item_id}/{mode}`）是 C/D 链路的职责
/// （30-学生端 WP-E 功能点 3 的约定）；本文件只负责入口与分发。
library;

import 'package:flutter/material.dart';

import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../features/home/practice_entry.dart';
import '../../features/recite/spell_session.dart';
import '../../router/routes/dictation.dart';
import '../../router/routes/recite.dart';
import '../../router/routes/wrongbook.dart';
import '../dictation/dictation_models.dart';
import 'wrongbook_models.dart';

/// 进一个重做组的原链路。返回是否成功进入（失败时已上屏中文提示）。
///
/// 调用方按钮自带 `_busy` + finally（契约 §9 学生端规范）；
/// 本函数只负责组卷、导航与错误上屏。
Future<bool> launchRedoGroup(
  BuildContext context, {
  required ApiClient api,
  required RedoGroup group,
}) async {
  final messenger = ScaffoldMessenger.maybeOf(context);
  try {
    switch (group.mode) {
      case 'listen':
        // 听写链路：错题直接当题单（J3：不改 D 的任何文件）。
        if (!context.mounted) {
          return false;
        }
        await Navigator.of(context).pushNamed(
          DictationRoutes.settings,
          arguments: DictationSessionArgs(
            items: [
              for (final it in group.items)
                DictationItem(id: it.itemId, en: it.en, zh: it.zh, pos: it.pos),
            ],
            unitName: group.unitName,
            returnRoute: WrongbookRoutes.wrongbook,
          ),
        );
        return true;
      case 'spell_zh2en':
      case 'spell_en2zh':
        // 背诵链路：start 拿真会话 → 过滤成本组错题 → 进答题页。
        final run = await startSpellRun(
          api,
          selection: PracticeSelection(
            unitId: group.unitId,
            unitName: group.unitName,
            type: group.type,
          ),
          mode: group.mode,
        );
        final wanted = group.items.map((it) => it.itemId).toSet();
        final redoItems =
            run.items.where((it) => wanted.contains(it.id)).toList();
        if (redoItems.isEmpty) {
          // 防御：错题内容被后台删掉后 start 组卷里对不上号，不进空场次。
          messenger?.showSnackBar(
            const SnackBar(
              content: Text('这道题的内容已经不在了，先把它移出错题本吧'),
              behavior: SnackBarBehavior.floating,
            ),
          );
          return false;
        }
        if (!context.mounted) {
          return false;
        }
        await Navigator.of(context).pushNamed(
          ReciteRoutes.spell,
          arguments: SpellRun(
            sessionId: run.sessionId,
            mode: run.mode,
            unitName: run.unitName,
            typeName: group.type,
            items: redoItems,
            startedAt: DateTime.now(),
            // J3：结算后回错题本，重做判对的条目已自动移出。
            returnRoute: WrongbookRoutes.wrongbook,
          ),
        );
        return true;
      default:
        // 契约 §7 之外的 mode：不猜语义，当解析失败处理。
        throw ApiException.parse(null);
    }
  } on ApiException catch (e) {
    // 20004 无可练内容等业务错误：直接上屏服务端中文消息。
    messenger?.showSnackBar(
      SnackBar(
        content: Text(e.displayText),
        behavior: SnackBarBehavior.floating,
      ),
    );
  } on Object {
    messenger?.showSnackBar(
      const SnackBar(
        content: Text('没能开始这次重做，请再试一次'),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }
  return false;
}
