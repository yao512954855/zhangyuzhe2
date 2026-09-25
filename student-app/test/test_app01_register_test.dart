/// WP-APP-01 注册页 · 班级选择（F15 / 契约 §4.1 补充 `GET /api/classes/options`）。
///
/// 这组护栏针对一个**真实存在过的缺陷**：注册页从来**没有把 `class_id` 传给后端**
/// —— `AuthController.register()` 里早就有 `classId` 参数，注册页却一次都没用过，
/// 于是学生在界面上根本选不了班。
///
/// 修法有两半，两条都在这里钉住：
/// 1. 后端补一条**免鉴权**的班级清单接口 —— 注册发生在登录之前，带鉴权的接口
///    永远返回 10002，注册页拿不到任何班级；
/// 2. 注册页渲染卡片列表，并把选中的班**真正提交**到 `/api/auth/register`。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/net/api_client.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/router/app_router.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/features/auth/register_page.dart';

/// 假 API：拦截「班级清单」与「注册」两条真实请求，其余照常交给真实实现。
class _FakeApi extends ApiClient {
  _FakeApi({
    required super.serverStore,
    required super.localStore,
    this.options = const [],
    this.optionsFail = false,
  });

  final List<Map<String, dynamic>> options;
  final bool optionsFail;

  /// 记录注册请求体，供「选的班有没有真传上去」断言。
  final List<Map<String, dynamic>> registerBodies = [];
  final List<String> requestedPaths = [];

  @override
  Future<T?> get<T>(
    String path, {
    Map<String, dynamic>? query,
    RequestOptionsOverride? override,
  }) {
    requestedPaths.add(path);
    if (path == '/api/classes/options') {
      if (optionsFail) {
        // 与真实 ApiClient 一致：网络类失败抛 ApiException（provider 内部吞掉）
        throw ApiException.network();
      }
      return Future<T?>.value(<String, dynamic>{'items': options} as T);
    }
    return super.get<T>(path, query: query, override: override);
  }

  @override
  Future<T?> post<T>(
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    RequestOptionsOverride? override,
  }) {
    requestedPaths.add(path);
    if (path == '/api/auth/register') {
      registerBodies.add(Map<String, dynamic>.from(body as Map));
      return Future<T?>.value(<String, dynamic>{
        'access_token': 'fake-token',
        'user': {
          'id': 1,
          'username': '20260099',
          'real_name': '小明',
          'class_id': 9,
        },
      } as T);
    }
    return super.post<T>(path, body: body, query: query, override: override);
  }
}

const _options = [
  {'id': 7, 'name': '三年级二班', 'grade': 3, 'semester': 'first'},
  {'id': 9, 'name': '三年级四班', 'grade': 3, 'semester': 'second'},
];

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore store;

  setUpAll(() async {
    SharedPreferences.setMockInitialValues({});
    local = await LocalStore.open();
    store = ServerStore(local);
  });

  setUp(() {
    // 默认 800×600 会让 ListView 懒加载 + 字号溢出，按 UI 规范 §9 设成手机视口。
    final view = TestWidgetsFlutterBinding.instance.platformDispatcher.implicitView!;
    view.physicalSize = const Size(1080, 1920);
    view.devicePixelRatio = 1.0;
  });

  tearDown(() {
    final view = TestWidgetsFlutterBinding.instance.platformDispatcher.implicitView!;
    view.resetPhysicalSize();
    view.resetDevicePixelRatio();
  });

  Widget wrap(Widget child, _FakeApi api) {
    return ProviderScope(
      overrides: [
        localStoreProvider.overrideWithValue(local),
        serverStoreProvider.overrideWithValue(store),
        apiClientProvider.overrideWithValue(api),
      ],
      child: MaterialApp(
        theme: AppTheme.build(palette: AppPalettes.day),
        // 注册成功会 `pushReplacementNamed(homeRoute)`；裸 MaterialApp 里没有该路由
        // 会抛异常。这里给个空页兜住 —— 断言只看请求体，不看跳转后的页面。
        routes: {AppRouter.homeRoute: (_) => const SizedBox.shrink()},
        home: AgeScope(profile: AgeProfile.ofGrade(3), child: child),
      ),
    );
  }

  testWidgets('拉到的班级渲染成卡片（带年级学期副标题），并保留「先不选」', (tester) async {
    final api = _FakeApi(serverStore: store, localStore: local, options: _options);
    await tester.pumpWidget(wrap(const RegisterPage(), api));
    await tester.pumpAndSettle();

    expect(api.requestedPaths, contains('/api/classes/options'));
    expect(find.text('三年级二班'), findsOneWidget);
    expect(find.text('三年级四班'), findsOneWidget);
    expect(find.text('3 年级 · 上学期'), findsOneWidget, reason: '同名班靠副标题区分');
    expect(find.text('3 年级 · 下学期'), findsOneWidget);
    expect(find.text('先不选'), findsOneWidget);
  });

  testWidgets('班级清单拉不到时不报错，只留「先不选」，注册不被阻断', (tester) async {
    final api = _FakeApi(serverStore: store, localStore: local, optionsFail: true);
    await tester.pumpWidget(wrap(const RegisterPage(), api));
    await tester.pumpAndSettle();

    expect(find.text('先不选'), findsOneWidget);
    expect(find.textContaining('三年级'), findsNothing, reason: '不伪造班级');
    expect(find.text('注册并开始'), findsOneWidget, reason: '拉不到班级不能卡住注册');
  });

  testWidgets('选中班级后提交，class_id 真的进了请求体（这正是曾经的缺陷）', (tester) async {
    final api = _FakeApi(serverStore: store, localStore: local, options: _options);
    await tester.pumpWidget(wrap(const RegisterPage(), api));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).at(0), '20260099');
    await tester.enterText(find.byType(TextField).at(1), '小明');
    await tester.enterText(find.byType(TextField).at(2), 'abc123456');

    await tester.tap(find.text('三年级四班'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('注册并开始'));
    await tester.pumpAndSettle();

    expect(api.registerBodies, hasLength(1), reason: '应当真的发出注册请求');
    expect(
      api.registerBodies.single['class_id'],
      9,
      reason: '选中的班必须交给后端（此前该字段一次都没被传过）',
    );
    expect(api.registerBodies.single['username'], '20260099');
  });

  testWidgets('「先不选」时请求体不带 class_id（契约 §2.2：该字段可选）', (tester) async {
    final api = _FakeApi(serverStore: store, localStore: local, options: _options);
    await tester.pumpWidget(wrap(const RegisterPage(), api));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).at(0), '20260098');
    await tester.enterText(find.byType(TextField).at(1), '小红');
    await tester.enterText(find.byType(TextField).at(2), 'abc123456');

    await tester.tap(find.text('注册并开始'));
    await tester.pumpAndSettle();

    expect(api.registerBodies, hasLength(1));
    expect(
      api.registerBodies.single.containsKey('class_id'),
      isFalse,
      reason: '没选班就不该发这个字段',
    );
  });
}
