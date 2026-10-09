/// 路由聚合器 —— 30-APP-01 F18 / 总纲 §5.4「不修改他人文件」。
///
/// **协议（后续 4 个 App 包都要遵守，也是本文件唯一需要知道的规则）**：
/// - 每个 feature 一个路由文件，放在 `lib/router/routes/xxx.dart`；
/// - 文件里导出 `abstract final class XxxRoutes`，含路径常量 + `routes` getter；
/// - **新增页面只改自己那个文件**，永远不动本文件。
///
/// 为此，APP-01 已经把 APP-02 ~ APP-05 的路由文件**全部建好占位**
/// （空的 `routes` getter + 常量），所以这里可以一次性 import 齐，
/// 不会出现"文件不存在编译不过"的问题。
library;

import 'package:flutter/material.dart';

import '../../features/bootstrap/not_found_page.dart';
import '../../router/routes/bootstrap.dart';
import '../../router/routes/discovery.dart';
import '../../router/routes/home.dart';
import '../../router/routes/mistakes.dart';
import '../../router/routes/practice.dart';
import '../../router/routes/recite.dart';

/// 全局路由表。
abstract final class AppRouter {
  /// 启动入口。
  static const String initialRoute = BootstrapRoutes.bootstrap;

  /// 全部命名路由。
  static Map<String, WidgetBuilder> get routes => {
        ...BootstrapRoutes.routes,
        ...DiscoveryRoutes.routes,
        ...HomeRoutes.routes,
        ...PracticeRoutes.routes,
        ...ReciteRoutes.routes,
        ...MistakesRoutes.routes,
      };

  static const String discoveryRoute = DiscoveryRoutes.setup;

  /// 落地页（简化版：真首页常驻，临时落地页已随 WP-F 删除）。
  static String get homeRoute => HomeRoutes.home;

  /// 真首页是否已经接管（调试/测试用）。
  static bool get homeIsReal => routes.containsKey(HomeRoutes.home);

  /// 未知路由兜底（F19：不出现红屏）。
  static Route<dynamic> onUnknownRoute(RouteSettings settings) {
    return MaterialPageRoute<void>(
      settings: settings,
      builder: (_) => NotFoundPage(routeName: settings.name),
    );
  }
}
