/// 服务器发现路由 —— WP-APP-01 所有。
library;

import 'package:flutter/material.dart';

import '../../features/discovery/server_setup_page.dart';

/// P1 服务器发现。
abstract final class DiscoveryRoutes {
  static const setup = '/server-setup';

  static Map<String, WidgetBuilder> get routes => {
        setup: (context) => ServerSetupPage(
              // 从发现页被主动打开（而非启动流程）时不做自动查找，
              // 直接让学生选择扫码/手输，不让他白等 6 秒广播。
              autoFind: (ModalRoute.of(context)?.settings.arguments as bool?) ?? true,
            ),
      };
}
