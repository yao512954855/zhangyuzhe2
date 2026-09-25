/// ⚠️ **本文件归 WP-APP-03 所有**（英语练习页），APP-01 只创建空壳。
///
/// 填法与 `home.dart` 相同：保留常量名、只填 [routes]、不改聚合器。
///
/// 重要说明（交付说明会再次点名）：WP-APP-03 规格清单写的是
/// `lib/router/routes/english.dart`，但那个文件**不存在、也未被聚合器 import**。
/// APP-01 已为所有包建好占位路由文件，英语练习页的真实占位就是本文件
/// `practice.dart`（`PracticeRoutes`，常量 `practice='/practice'`、`summary='/practice/summary'`，
/// 且 `core/router/app_router.dart` 已 `import` 它并 `...PracticeRoutes.routes`）。
/// 因此本包**必须填这里**，新建 `english.dart` 只会成为死代码、且不被任何地方引用。
/// 详见交付说明偏离项 D-APP03-01。
library;

import 'package:flutter/material.dart';

import '../../features/english/practice_page.dart';
import '../../features/english/settle_page.dart';
import '../../features/config/practice_session.dart';

/// P6 / P7 / P9：英语说单词页、拼读页、结算页。
abstract final class PracticeRoutes {
  /// P6 英语 · 说单词 / P7 拼读。
  static const practice = '/practice';

  /// P9 结算页。
  static const summary = '/practice/summary';

  static Map<String, WidgetBuilder> get routes => {
        // 参数从 `ModalRoute.settings.arguments` 取（与 home.dart 同款写法）。
        // 缺失时给「请回首页再点一次」的友好兜底，**绝不崩**。
        practice: (context) {
          final args = ModalRoute.of(context)?.settings.arguments;
          return PracticePage(
            session: args is PracticeSession ? args : const PracticeSession(sessionId: 0, items: []),
          );
        },
        summary: (context) {
          final args = ModalRoute.of(context)?.settings.arguments;
          return SettlePage(args: args is SettleArguments ? args : const SettleArguments.empty());
        },
      };
}
