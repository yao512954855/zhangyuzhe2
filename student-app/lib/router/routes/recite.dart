/// 背诵路由 —— 简化版 WP-C 重写（清空旧语文背诵页）。
///
/// 新链路：`spellConfig`（选模式）→ `spell`（逐字母/说中文答题）→
/// `settle`（总结算，3 秒自动回单元列表）。参数经 `settings.arguments`
/// 传递（`PracticeSelection` / `SpellRun` / `SettleSummary`）。
///
/// 旧常量 `recite = '/recite'` 保留：`features/config/practice_launcher.dart`
/// （孤儿文件，本批次暂留）还在引用；不注册页面。
library;

import 'package:flutter/material.dart';

import '../../features/recite/settle_page.dart';
import '../../features/recite/spell_config_page.dart';
import '../../features/recite/spell_page.dart';

/// 背诵链路路径常量。
abstract final class ReciteRoutes {
  /// 模式选择页（看中文背英文 / 看英文背中文）。
  static const spellConfig = '/recite/config';

  /// 答题页。
  static const spell = '/recite/spell';

  /// 总结算页。
  static const settle = '/recite/settle';

  /// 旧语文背诵页路径（孤儿引用保留，无注册页面）。
  static const recite = '/recite';

  static Map<String, WidgetBuilder> get routes => {
        spellConfig: (_) => const SpellConfigPage(),
        spell: (_) => const SpellPage(),
        settle: (_) => const SettlePage(),
      };
}
