/// ⚠️ **本文件归 WP-APP-04 所有**（语文背诵页），APP-01 只创建空壳。
library;

import 'package:flutter/material.dart';

import '../../features/config/practice_session.dart';
import '../../features/recite/recite_page.dart';

/// P8：语文 · 背诵页（逐字点亮）。
abstract final class ReciteRoutes {
  /// P8 背诵页。
  static const recite = '/recite';

  static Map<String, WidgetBuilder> get routes => {
        recite: (context) {
          // 入口 `openPracticeSession` 把 `PracticeSession` 作为路由参数传入。
          // 缺参时如实给引导（不崩、不假装成功）。
          final args = ModalRoute.of(context)?.settings.arguments;
          final session = args is PracticeSession ? args : null;
          return RecitePage(session: session);
        },
      };
}
