/// 首页 / 听背入口路由 —— 简化版 WP-C 重写。
///
/// 本批次首页只剩【听背】【错题本】两个入口（30-学生端 WP-C 功能点 1）：
/// - `home`：新首页（**常量名不能改**——`AppRouter.homeRoute`/`homeIsReal`
///   靠它判断真首页已接管）；
/// - `unitList`：单元列表 + 类型单选（听/背分发）。
///
/// 旧多用户体系路由（gradePicker / unitDetail / practiceConfig）**常量保留**：
/// `features/config/`（本批次暂不物理删除的孤儿）与旧页面文件还在引用它们；
/// 页面注册已清空，物理删除清单见 WP-C 批次3 报告接力项。
library;

import 'package:flutter/material.dart';

import '../../features/home/home_page.dart';
import '../../features/home/unit_list_page.dart';

/// 首页与听背入口路径常量。
abstract final class HomeRoutes {
  /// 首页。**常量名不要改**。
  static const home = '/home';

  /// 听背 · 单元列表（新链路）。
  static const unitList = '/home/units';

  /// 旧体系常量（仅存量文件引用，无注册页面）。
  static const unitDetail = '/home/unit';
  static const gradePicker = '/home/grade';
  static const practiceConfig = '/home/config';

  static Map<String, WidgetBuilder> get routes => {
        home: (_) => const HomePage(),
        unitList: (_) => const UnitListPage(),
      };
}
