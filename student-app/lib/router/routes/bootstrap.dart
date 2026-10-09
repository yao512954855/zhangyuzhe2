/// 启动页路由 —— WP-APP-01 所有（简化版已拆临时落地页）。
library;

import 'package:flutter/material.dart';

import '../../features/bootstrap/bootstrap_page.dart';

/// 启动与落地。
abstract final class BootstrapRoutes {
  /// 启动引导页（App 入口）。
  static const bootstrap = '/';

  static Map<String, WidgetBuilder> get routes => {
        bootstrap: (_) => const BootstrapPage(),
      };
}
