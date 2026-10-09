/// 听写链路路由表 —— WP-D（路由**注册**进 `app_router.dart` 是接力项 J1，
/// 归 WP-C 收口；本文件只声明路由名与构建方式，D 不得改 app_router.dart）。
library;

import 'package:flutter/material.dart';

import '../../features/dictation/dictation_capture_page.dart';
import '../../features/dictation/dictation_models.dart';
import '../../features/dictation/dictation_play_page.dart';
import '../../features/dictation/dictation_result_page.dart';
import '../../features/dictation/dictation_settings_page.dart';

/// 听写链路路由名（J1 接线时把这四条注册进 app_router.dart）。
abstract final class DictationRoutes {
  /// 听写设置页（入口；参数：[DictationSessionArgs]）。
  static const settings = '/dictation/settings';

  /// 播报页（参数：[DictationSessionArgs] + settings）。
  static const play = '/dictation/play';

  /// 拍照批改页（一般由播报页自动进入，不手动导航）。
  static const capture = '/dictation/capture';

  /// 结果页（由拍照批改成功后自动进入）。
  static const result = '/dictation/result';

  /// 供 J1 注册的路由表。
  ///
  /// ⚠️ 页面之间实际用直接 push（可构造、可测试）；命名路由只服务
  /// 「从单元列表进入设置页」与「结果页 pushNamedAndRemoveUntil 回跳」
  /// 两个跨 feature 的口子。
  static Map<String, WidgetBuilder> get routes => {
        settings: (context) {
          final args = ModalRoute.of(context)?.settings.arguments;
          final session = args is DictationSessionArgs ? args : null;
          return DictationSettingsPage(
            items: session?.items ?? const [],
            unitName: session?.unitName ?? '',
            returnRoute: session?.returnRoute,
          );
        },
        play: (context) {
          final args = ModalRoute.of(context)?.settings.arguments;
          final session = args is DictationSessionArgs ? args : null;
          return DictationPlayPage(
            items: session?.items ?? const [],
            unitName: session?.unitName ?? '',
            returnRoute: session?.returnRoute,
            settings: const DictationSettings(),
          );
        },
        capture: (context) {
          final args = ModalRoute.of(context)?.settings.arguments;
          final session = args is DictationSessionArgs ? args : null;
          return DictationCapturePage(
            items: session?.items ?? const [],
            unitName: session?.unitName ?? '',
            returnRoute: session?.returnRoute,
          );
        },
        result: (context) {
          final args = ModalRoute.of(context)?.settings.arguments;
          return DictationResultPage(
            report: args is DictationGradeReport
                ? args
                : const DictationGradeReport(results: [], ocrContent: ''),
            items: const [],
          );
        },
      };
}
