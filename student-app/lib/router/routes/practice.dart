/// ⚠️ 简化版（WP-F 拆除英语练习页后）—— 本文件降级为**常量壳**。
///
/// 旧多用户系统的 `features/english/`（说单词 / 拼读 / 结算页）已随 WP-F F-2 删除，
/// `routes` getter 相应清空；保留 `PracticeRoutes` 类名与路径常量，
/// 是因为 `features/config/practice_launcher.dart` 还在用它们做「是否已注册」判断
/// （未注册时走「还在开发中」弹层兜底，绝不崩）。
/// WP-C 重写背链路后如需新练习路由，在 WP-C 的文件里重新登记。
library;

import 'package:flutter/widgets.dart';

/// P6 / P7 / P9 路径常量（当前无注册页面）。
abstract final class PracticeRoutes {
  /// P6 英语 · 说单词 / P7 拼读。
  static const practice = '/practice';

  /// P9 结算页。
  static const summary = '/practice/summary';

  static Map<String, WidgetBuilder> get routes => {};
}
