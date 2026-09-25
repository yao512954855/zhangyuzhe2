/// 错题本 / 我的 路由 —— WP-APP-05。
///
/// 主壳 `main_shell.dart` 在运行时检查这里的 `routes`：
/// - `/mistakes`       → 「错题本」底部 Tab 内容（也是「我的」总入口）
/// - `/mistakes/records` → 「我的」底部 Tab 内容
/// - `/mistakes/settings` → 设置页
///
/// ⚠️ 红线 1：独占文件只动这里，**保留 `MistakesRoutes` 类名与三个常量**，
/// 只填 `routes` getter（不新建 `mine.dart`）。
library;

import 'package:flutter/widgets.dart';

import '../../features/mine/mine_page.dart';
import '../../features/mine/my_records_page.dart';
import '../../features/mine/settings_page.dart';

abstract final class MistakesRoutes {
  static const mistakes = '/mistakes';
  static const records = '/mistakes/records';
  static const settings = '/mistakes/settings';

  static Map<String, WidgetBuilder> get routes => <String, WidgetBuilder>{
    mistakes: (context) => const MinePage(),
    records: (context) => const MyRecordsPage(),
    settings: (context) => const SettingsPage(),
  };
}
