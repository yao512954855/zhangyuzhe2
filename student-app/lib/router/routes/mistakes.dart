/// 错题本 / 我的 路由 —— 简化版（WP-F 拆除旧「我的」体系后）。
///
/// 旧多用户系统的 `features/mine/` 已随 WP-F F-2 删除，`routes` getter 清空。
/// ⚠️ **保留 `MistakesRoutes` 类名与三个常量**：主壳 `main_shell.dart`
/// 在运行时检查这里的 `routes`（未注册时对应 Tab 自动不渲染，不崩）；
/// WP-E（错题本）落地时在本文件重新登记页面。
library;

import 'package:flutter/widgets.dart';

abstract final class MistakesRoutes {
  static const mistakes = '/mistakes';
  static const records = '/mistakes/records';
  static const settings = '/mistakes/settings';

  static Map<String, WidgetBuilder> get routes => <String, WidgetBuilder>{};
}
