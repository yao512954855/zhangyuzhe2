/// 启动页路由 —— WP-APP-01 所有。
library;

import 'package:flutter/material.dart';

import '../../features/bootstrap/bootstrap_page.dart';
import '../../features/bootstrap/home_placeholder_page.dart';

/// 启动与临时落地。
abstract final class BootstrapRoutes {
  /// 启动引导页（App 入口）。
  static const bootstrap = '/';

  /// **临时**落地页：APP-02 注册 `/home` 之后即失效（见 `AppRouter.homeRoute`）。
  static const homePlaceholder = '/__home_placeholder';

  static Map<String, WidgetBuilder> get routes => {
        bootstrap: (_) => const BootstrapPage(),
        homePlaceholder: (_) => const HomePlaceholderPage(),
      };
}
