/// WP-APP-05 测试共享辅助 —— 不是测试用例（文件名不以 `_test.dart` 结尾，
/// `flutter test` 不会把它当用例跑）。
///
/// 复用 APP-02 的假件基础设施（`support/fake_home_repository.dart` 的
/// `clientWith` / `JsonAdapter` / `okBody` / `useTallScreen`），
/// 这里只补「APP-05 专属」的 boot 包装与数据夹具。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';

import 'support/fake_home_repository.dart';

// 让各测试文件无需各自 import 这些符号。
export 'package:student_app/core/theme/app_theme.dart' show AppTheme, AppPalettes, AppThemeId;
export 'package:student_app/core/theme/age_profile.dart' show AgeProfile, AgeBand, AgeScope;
export 'package:student_app/core/storage/local_store.dart' show LocalStore;
export 'package:student_app/core/storage/server_store.dart' show ServerStore;
export 'support/fake_home_repository.dart' show useTallScreen, JsonAdapter, okBody;

const String kTestServerHost = '127.0.0.1';
const int kTestServerPort = 8810;

/// 给测试准备一份「已登录 + 已配置服务器地址」的本地存储。
///
/// 关键：服务器地址**预先写好**，这样退出登录后还能断言它"被保留"。
Future<(LocalStore, ServerStore)> openTestStore() async {
  SharedPreferences.setMockInitialValues({
    StoreKeys.serverHost: kTestServerHost,
    StoreKeys.serverPort: kTestServerPort,
    StoreKeys.authToken: 'test-token',
    StoreKeys.authUserId: 7,
    StoreKeys.authUsername: 'student07',
    StoreKeys.authRealName: '张同学',
  });
  final local = await LocalStore.open();
  final serverStore = ServerStore(local);
  return (local, serverStore);
}

/// 把页面包进一个可独立运行的最小 App。
///
/// - 注入真实 `LocalStore`（含服务器地址），让 `serverStoreProvider` / `eyeCareProvider`
///   / `themeControllerProvider` 都能从它读；
/// - 可选注入 `JsonAdapter` 替换网络层（只换 adapter，仍走生产 `ApiClient` 解包）；
/// - `onGenerateRoute` 兜底所有命名路由（发现页 / 登录页 / 练习页），
///   避免"未落地页面"在测试里崩，同时让 `NavigatorObserver` 能抓到真实的 push 行为；
/// - `AgeScope` 包住页面，满足分龄主题读取。
Widget bootApp(
  Widget page, {
  required LocalStore local,
  required ServerStore serverStore,
  JsonAdapter? adapter,
  List<NavigatorObserver>? observers,
}) {
  return ProviderScope(
    overrides: [
      localStoreProvider.overrideWithValue(local),
      if (adapter != null)
        apiClientProvider.overrideWithValue(
          clientWith(adapter, serverStore: serverStore, localStore: local),
        ),
    ],
    child: MaterialApp(
      theme: AppTheme.build(palette: AppPalettes.day),
      navigatorObservers: observers ?? const [],
      onGenerateRoute: (settings) => MaterialPageRoute<void>(
        builder: (_) => Scaffold(
          body: Center(child: Text('route:${settings.name}')),
        ),
        settings: settings,
      ),
      home: AgeScope(profile: AgeProfile.of(AgeBand.junior), child: page),
    ),
  );
}

/// 记 push / pushNamed 的 observer —— 用来断言"跳到了哪个路由"（而非真的渲染它）。
class PushSpy extends NavigatorObserver {
  final List<Route<dynamic>> pushed = [];
  final List<String?> pushedNames = [];
  final List<Route<dynamic>> popped = [];

  @override
  void didPush(Route<dynamic> route, Route<dynamic>? previousRoute) {
    pushed.add(route);
    pushedNames.add(route.settings.name);
    super.didPush(route, previousRoute);
  }

  @override
  void didPop(Route<dynamic> route, Route<dynamic>? previousRoute) {
    popped.add(route);
    super.didPop(route, previousRoute);
  }
}

/// 默认进度夹具（F2 / F3 渲染用）。
Map<String, dynamic> progressJson({
  int totalWords = 120,
  int practicedWords = 80,
  int masteredWords = 50,
  int streakDays = 7,
  double accuracy = 0.85,
  int todaySessions = 2,
  int todayItems = 20,
  int todayCorrect = 18,
}) =>
    {
      'total_words': totalWords,
      'practiced_words': practicedWords,
      'mastered_words': masteredWords,
      'total_texts': 0,
      'recited_texts': 0,
      'streak_days': streakDays,
      'accuracy': accuracy,
      'today': {
        'sessions': todaySessions,
        'items': todayItems,
        'correct': todayCorrect,
      },
    };

/// 构造 `GET /api/me/wrong-words` 的契约响应体（items 已是 "按错误次数降序" 的服务端序）。
Map<String, dynamic> wrongWordsJson(List<Map<String, dynamic>> items, int total) =>
    {
      'items': items,
      'total': total,
    };

/// 一条错题的字段（默认英语，wrong_count 给 1）。
Map<String, dynamic> wrongItemJson({
  required int wordId,
  required int unitId,
  required String unitName,
  String? en,
  String? cn,
  String? phonetic,
  int wrongCount = 1,
  int totalCount = 1,
  String? lastWrongAt,
  String? lastYourAnswer,
  String? masteryStatus,
}) =>
    {
      'word_id': wordId,
      'unit_id': unitId,
      'unit_name': unitName,
      'type': 'word',
      // Dart 的 null-aware element：`?en` 等价于 `if (en != null) 'en': en`。
      'en': ?en,
      'cn': ?cn,
      'phonetic': ?phonetic,
      'wrong_count': wrongCount,
      'total_count': totalCount,
      'last_wrong_at': ?lastWrongAt,
      'last_your_answer': ?lastYourAnswer,
      'mastery_status': ?masteryStatus,
    };

/// 一个能给 `/api/me/progress` / `/api/me/wrong-words` / `/api/ping`
/// 都回默认值的 adapter（各用例再按需要覆盖）。
JsonAdapter defaultAdapter({
  Map<String, dynamic>? progress,
  Map<String, dynamic>? wrongWords,
}) {
  final prog = progress ?? progressJson();
  final wrong = wrongWords ?? wrongWordsJson(const [], 0);
  return JsonAdapter((options) {
    switch (options.uri.path) {
      case '/api/me/progress':
        return okBody(prog);
      case '/api/me/wrong-words':
        return okBody(wrong);
      case '/api/sessions':
        return okBody({
          'session_id': 9001,
          'unit_id': 11,
          'unit_name': 'Unit 1',
          'content_type': 'english',
          'items': [
            {
              'seq': 1,
              'word_id': 88,
              'mode': 'cn2en',
              'prompt': '教室',
            },
          ],
          'time_limit_ms': 0,
          'started_at': '2026-09-24T09:00:00Z',
        });
      default:
        return okBody(const {});
    }
  });
}
