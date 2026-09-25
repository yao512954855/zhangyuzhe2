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

/// 构造 `GET /api/me/wrong-chars` 的契约响应体（接口设计 §8.10，语文错题本）。
///
/// 与英语的 [wrongWordsJson] **是两个端点、两套字段** —— 别互相套用。
Map<String, dynamic> wrongCharsJson(List<Map<String, dynamic>> items, int total) =>
    {
      'items': items,
      'total': total,
    };

/// 一条错字（`GET /api/me/wrong-chars` 的 `items[]`）。
///
/// `context_before` / `context_after` 由**服务端**从 `content_plain` 截好，
/// 客户端不自己算 —— 所以夹具里也照契约给。
Map<String, dynamic> wrongCharItemJson({
  required int textId,
  required String char,
  int unitId = 11,
  String textTitle = '背影',
  String unitName = '第1单元',
  int wrongCount = 1,
  String? lastWrongAt,
  int firstPos = 0,
  String contextBefore = '',
  String contextAfter = '',
}) =>
    {
      'text_id': textId,
      'text_title': textTitle,
      'unit_id': unitId,
      'unit_name': unitName,
      'char': char,
      'wrong_count': wrongCount,
      'last_wrong_at': ?lastWrongAt,
      'first_pos': firstPos,
      'context_before': contextBefore,
      'context_after': contextAfter,
    };

/// 构造 `GET /api/sessions`（接口设计 §9.5.1）的一条场次摘要。
///
/// `ongoing` 场次按契约给 `score: null` / `ended_at: null` / `duration_ms: 0`
/// —— 调用方显式传 `null` 覆盖默认值（`score` 的类型是 `double?`）。
Map<String, dynamic> sessionRecordJson({
  required int sessionId,
  String contentType = 'english',
  int? unitId = 11,
  String? unitName = 'Unit 1',
  String status = 'finished',
  int totalItems = 20,
  int correctItems = 18,
  double accuracy = 0.9,
  double? score = 88.5,
  int hintCount = 1,
  String? startedAt = '2026-09-24T09:00:00Z',
  String? endedAt = '2026-09-24T09:01:36Z',
  int durationMs = 96000,
}) =>
    {
      'session_id': sessionId,
      'content_type': contentType,
      'unit_id': unitId,
      'unit_name': unitName,
      'status': status,
      'total_items': totalItems,
      'correct_items': correctItems,
      'accuracy': accuracy,
      'score': score,
      'hint_count': hintCount,
      'started_at': startedAt,
      'ended_at': endedAt,
      'duration_ms': durationMs,
    };

/// 构造 `GET /api/sessions` 的分页响应体。
Map<String, dynamic> sessionsJson(
  List<Map<String, dynamic>> items,
  int total, {
  int page = 1,
  int pageSize = 20,
}) =>
    {
      'page': page,
      'page_size': pageSize,
      'total': total,
      'items': items,
    };

/// 英语场次的一条逐项明细（§9.5.2）。
Map<String, dynamic> sessionItemJson({
  int? wordId = 88,
  String? en = 'classroom',
  String? cn = '教室',
  String state = 'correct',
  String? yourAnswer = 'classroom',
  String? expected = 'classroom',
  String? matchType = 'exact',
  List<int> steps = const [1],
  int hintLevel = 0,
  bool usedKeyboard = false,
}) =>
    {
      'word_id': wordId,
      'en': en,
      'cn': cn,
      'state': state,
      'your_answer': yourAnswer,
      'expected': expected,
      'match_type': matchType,
      'steps': steps,
      'hint_level': hintLevel,
      'used_keyboard': usedKeyboard,
    };

/// 语文场次的一篇背诵明细（含服务端重建的漏字，§9.5.2）。
Map<String, dynamic> recitationJson({
  required int recitationId,
  int? textId = 7,
  String? title = '静夜思',
  int totalChars = 40,
  int matchedChars = 38,
  double coveredRatio = 0.95,
  String? grade = '良好',
  double? score = 86.0,
  int durationMs = 45000,
  int restartCount = 0,
  String? finishReason = 'completed',
  List<Map<String, dynamic>> missedDetail = const [],
}) =>
    {
      'recitation_id': recitationId,
      'text_id': textId,
      'title': title,
      'total_chars': totalChars,
      'matched_chars': matchedChars,
      'covered_ratio': coveredRatio,
      'grade': grade,
      'score': score,
      'duration_ms': durationMs,
      'restart_count': restartCount,
      'finish_reason': finishReason,
      'missed_detail': missedDetail,
    };

/// 一条漏字（`pos` 是背诵范围内的下标，`char` 由服务端重建）。
Map<String, dynamic> missedCharJson(int pos, String? char) =>
    {'pos': pos, 'char': char};

/// 构造 `GET /api/sessions/{id}` 的详情体：英语给 `items`、语文给 `recitations`。
///
/// 两个学科**互斥**：不用的那一边显式给 `null`（不是"没取到"，是本来就没有）。
Map<String, dynamic> sessionDetailJson(
  Map<String, dynamic> record, {
  List<Map<String, dynamic>>? items,
  List<Map<String, dynamic>>? recitations,
}) =>
    {
      ...record,
      'items': items,
      'recitations': recitations,
    };

/// 一个能给 `/api/me/progress` / `/api/me/wrong-words` / `/api/sessions`
/// 都回默认值的 adapter（各用例再按需要覆盖）。
///
/// ⚠️ **`/api/sessions` 这个 path 有两种语义**：`GET` = 我的记录列表（§9.5.1），
/// `POST` = 创建场次（§9.1）。曾经这里**只按 path 匹配、不看方法**，
/// 于是列表请求会拿到"创建场次"的响应体，`items` 里那些 `{seq,mode,prompt}`
/// 被当成场次摘要悄悄解析掉 —— 页面不报错，只是永远显示不出记录
/// （假适配器不校验 schema 的老坑，见 §八）。
JsonAdapter defaultAdapter({
  Map<String, dynamic>? progress,
  Map<String, dynamic>? wrongWords,
  Map<String, dynamic>? wrongChars,
  Map<String, dynamic>? sessions,
  Map<String, dynamic>? sessionDetail,
}) {
  final prog = progress ?? progressJson();
  final wrong = wrongWords ?? wrongWordsJson(const [], 0);
  final chars = wrongChars ?? wrongCharsJson(const [], 0);
  final list = sessions ?? sessionsJson(const [], 0);
  return JsonAdapter((options) {
    final path = options.uri.path;
    final method = options.method.toUpperCase();

    if (path == '/api/sessions') {
      if (method == 'POST') {
        // 创建场次（§9.1）：这里的形状与"列表"完全不同，别混。
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
      }
      return okBody(list);
    }

    // `GET /api/sessions/{id}`（§9.5.2）。用正则锚定，避免把
    // `/api/sessions/9/answers` 之类的子路径也吞进来。
    final detail = RegExp(r'^/api/sessions/(\d+)$').firstMatch(path);
    if (detail != null && method == 'GET') {
      return okBody(sessionDetail ?? const <String, dynamic>{});
    }

    switch (path) {
      case '/api/me/progress':
        return okBody(prog);
      case '/api/me/wrong-words':
        return okBody(wrong);
      case '/api/me/wrong-chars':
        return okBody(chars);
      default:
        return okBody(const {});
    }
  });
}
