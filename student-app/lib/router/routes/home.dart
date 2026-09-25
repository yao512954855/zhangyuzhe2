/// 首页路由 —— **WP-APP-02 所有**（APP-01 只创建了空壳）。
///
/// 按 `app_router.dart` 的协议填：保留常量名、只填 [routes]、**不改聚合器**。
/// `AppRouter.homeRoute` 靠 [home] 这个常量判断"真首页已接管"，
/// 所以**常量名不能改** —— 改了聚合器就会继续指向 APP-01 的临时落地页。
library;

import 'package:flutter/material.dart';

import '../../features/config/practice_config_page.dart';
import '../../features/home/grade_semester_picker.dart';
import '../../features/home/main_shell.dart';
import '../../features/home/models.dart';
import '../../features/home/unit_detail_page.dart';

/// P4 首页、单元详情、年级学期、P5 练习配置。
abstract final class HomeRoutes {
  /// P4 首页。**常量名不要改**。
  static const home = '/home';

  /// 单元详情（词条列表与各自掌握状态，F14）。
  static const unitDetail = '/home/unit';

  /// 年级学期选择页（F2：仅首次强制）。
  static const gradePicker = '/home/grade';

  /// P5 练习配置页。
  static const practiceConfig = '/home/config';

  static Map<String, WidgetBuilder> get routes => {
        // 首页外壳：底部导航把「错题本 / 我的」也一并管起来（见 main_shell.dart）
        home: (_) => const MainShell(),
        gradePicker: (_) => const GradeSemesterPickerPage(),
        unitDetail: (context) {
          final args = ModalRoute.of(context)?.settings.arguments;
          // 参数缺失时**不崩**：页面自己会给"请回首页再点一次"的指引
          return UnitDetailPage(unit: args is UnitItem ? args : null);
        },
        practiceConfig: (context) {
          final args = ModalRoute.of(context)?.settings.arguments;
          if (args is PracticeConfigArgs) {
            return PracticeConfigPage(unit: args.unit, initialScope: args.scope);
          }
          // 兼容只传 UnitItem 的调用方（如单元详情页的「设置练法」）
          if (args is UnitItem) {
            return PracticeConfigPage(unit: args);
          }
          return const PracticeConfigPage();
        },
      };
}
