/// 错题本路由 —— 简化版 WP-E（30-学生端 WP-E 文件清单）。
///
/// 只声明路由名与构建方式；**注册进 `app_router.dart` 是接力项 J1**
/// （该文件归 WP-C 独占，本包不改），集成阶段加一行
/// `...WrongbookRoutes.routes` 即可。
library;

import 'package:flutter/material.dart';

import '../../features/wrongbook/wrongbook_page.dart';

/// 错题本路由名。
abstract final class WrongbookRoutes {
  /// 错题本列表页。
  static const wrongbook = '/wrongbook';

  /// 供 J1 注册的路由表。
  static Map<String, WidgetBuilder> get routes => {
        wrongbook: (_) => const WrongbookPage(),
      };
}
