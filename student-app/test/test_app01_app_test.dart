/// WP-APP-01 外壳与页面 —— 路由聚合、启动流程、主题切换（U1 / U7 / U9）。
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/app.dart';
import 'package:student_app/core/discovery/broadcast_finder.dart';
import 'package:student_app/core/net/api_client.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/router/app_router.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/core/theme/theme_controller.dart';
import 'package:student_app/features/auth/login_page.dart';
import 'package:student_app/features/theme/theme_switcher_sheet.dart';

/// 测试环境里不真的发 UDP（真实现会广播 3 轮、每轮等 2 秒）。
class _FakeFinder implements BroadcastFinder {
  _FakeFinder({this.supported = true, this.found});

  @override
  final bool supported;

  /// 不传 = 广播找不到（走扫码/手输）；传了 = 模拟广播命中。
  final ServerAddress? found;

  String? _error;

  @override
  String? get lastError => _error;

  @override
  Future<ServerAddress?> find({
    Duration timeout = kDiscoverTimeout,
    int retries = kDiscoverRetries,
    void Function(String status)? onStatus,
  }) async {
    onStatus?.call('正在寻找服务器…');
    final hit = found;
    if (hit != null) {
      return hit;
    }
    _error = supported ? '同一个网络里没有找到服务器，可以让管理者给你扫二维码' : '当前环境不支持自动查找';
    return null;
  }

  @override
  Future<void> dispose() async {}
}

/// 用假的 HTTP 适配器替掉真网络（发现页探活会真发 `/api/ping`）。
class _FakeAdapter implements HttpClientAdapter {
  _FakeAdapter(this.handler);

  final Future<ResponseBody> Function(RequestOptions options) handler;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) =>
      handler(options);

  @override
  void close({bool force = false}) {}
}

/// 一个「探活必成功」的适配器（`GET /api/ping` → code 0）。
_FakeAdapter _okPingAdapter() => _FakeAdapter(
      (_) async => ResponseBody.fromString(
        jsonEncode({
          'code': 0,
          'message': 'ok',
          'data': {
            'service': 'bjsys',
            'version': '1.0.0',
            'server_name': '背记系统',
          },
          'trace_id': 't-ping',
        }),
        200,
        headers: {
          Headers.contentTypeHeader: ['application/json'],
        },
      ),
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    local = await LocalStore.open();
  });

  Widget wrap(
    Widget child, {
    AgeBand band = AgeBand.junior,
  }) {
    return ProviderScope(
      overrides: [
        localStoreProvider.overrideWithValue(local),
        broadcastFinderProvider.overrideWithValue(_FakeFinder()),
      ],
      child: MaterialApp(
        theme: AppTheme.build(palette: AppPalettes.day),
        home: AgeScope(profile: AgeProfile.of(band), child: child),
      ),
    );
  }

  group('路由聚合（F18）', () {
    test('APP-01 的三个路由都已注册', () {
      expect(AppRouter.routes.containsKey('/'), isTrue);
      expect(AppRouter.routes.containsKey('/server-setup'), isTrue);
      expect(AppRouter.routes.containsKey('/login'), isTrue);
      expect(AppRouter.routes.containsKey('/register'), isTrue);
      expect(AppRouter.initialRoute, '/');
      expect(AppRouter.loginRoute, '/login');
      expect(AppRouter.discoveryRoute, '/server-setup');
    });

    test('APP-02 已接管落地页：homeRoute 自动切到真首页', () {
      // ⚠️ 本条原为「APP-02 尚未接管时指向临时页」，APP-02 落地后按协议翻转。
      //    APP-01 的这组断言是**时序快照**（描述"当时谁还没做"），
      //    不是契约 —— 契约只要求「注册了 /home 就切过去」。
      expect(AppRouter.homeIsReal, isTrue);
      expect(AppRouter.homeRoute, '/home');
      expect(AppRouter.routes.containsKey(AppRouter.homeRoute), isTrue);
    });

    test('各包路由都已接管（聚合器能安全读取）', () {
      // APP-02 已落地
      expect(AppRouter.routes.containsKey('/home'), isTrue);
      // APP-03 / APP-04 / APP-05 已落地（2026-09-25 集成时按本测试自己的约定翻转）。
      // ⚠️ 这组是**时序断言**，不是契约：契约只要求「包落地了就注册进来」。
      //    注意路由常量名沿用 APP-01 建的占位文件（practice / recite / mistakes），
      //    而不是各包规格里写的 english.dart / mine.dart —— 那两个文件不存在且未被聚合器
      //    import，新建它们只会是死代码（各包交付说明的偏离项 D-APP03-01 有记录）。
      expect(AppRouter.routes.containsKey('/practice'), isTrue);
      expect(AppRouter.routes.containsKey('/recite'), isTrue);
      expect(AppRouter.routes.containsKey('/mistakes'), isTrue);
    });

    test('未知路由有兜底页（F19 不红屏）', () {
      final route = AppRouter.onUnknownRoute(const RouteSettings(name: '/不存在'));
      expect(route, isA<MaterialPageRoute<dynamic>>());
    });
  });

  group('P1 服务器发现页（U1 / U4）', () {
    testWidgets('找了一圈没找到 → 展示扫码与手动输入，且不出现技术术语', (tester) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            localStoreProvider.overrideWithValue(local),
            broadcastFinderProvider.overrideWithValue(
              _FakeFinder(supported: false),
            ),
          ],
          child: MaterialApp(
            routes: AppRouter.routes,
            initialRoute: AppRouter.discoveryRoute,
            theme: AppTheme.build(palette: AppPalettes.day),
            builder: (context, child) => AgeScope(
              profile: AgeProfile.of(AgeBand.junior),
              child: child ?? const SizedBox.shrink(),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('扫码连接'), findsOneWidget);
      expect(find.text('手动输入地址'), findsOneWidget);
      expect(find.text('背记系统'), findsOneWidget);

      // 规范 §7.2：文案里不出现「IP」「端口」「网络」等术语
      final texts = tester
          .widgetList<Text>(find.byType(Text))
          .map((t) => t.data ?? '')
          .join(' | ');
      expect(texts.contains('IP'), isFalse, reason: '不该出现 IP 字样');
      expect(texts.contains('端口'), isFalse, reason: '不该出现 端口 字样');
      expect(texts, contains('二维码'));
    });

    testWidgets('扫码是主按钮（排在手输前面）', (tester) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            localStoreProvider.overrideWithValue(local),
            broadcastFinderProvider.overrideWithValue(_FakeFinder(supported: false)),
          ],
          child: MaterialApp(
            routes: AppRouter.routes,
            initialRoute: AppRouter.discoveryRoute,
            theme: AppTheme.build(palette: AppPalettes.day),
            builder: (context, child) => AgeScope(
              profile: AgeProfile.of(AgeBand.junior),
              child: child ?? const SizedBox.shrink(),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      final scanY = tester.getCenter(find.text('扫码连接')).dy;
      final manualY = tester.getCenter(find.text('手动输入地址')).dy;
      expect(scanY, lessThan(manualY), reason: '规范 §7.2：主按钮是扫码');
    });

    /// 复刻「换了地方 / IP 变了 → 连上 → 黑屏不跳转」这条真机路径。
    ///
    /// 关键前提：**发现页是导航栈底**。启动流程连不上时用的是
    /// `pushReplacementNamed(discoveryRoute)`，启动页已经不在栈里了，
    /// 旧实现无条件 `pop(true)` 会把整个栈弹空 → 黑屏。
    Widget bootstrapLikeApp({required BroadcastFinder finder}) {
      return ProviderScope(
        overrides: [
          localStoreProvider.overrideWithValue(local),
          broadcastFinderProvider.overrideWithValue(finder),
          apiClientProvider.overrideWith(
            (ref) => ApiClient(
              serverStore: ref.watch(serverStoreProvider),
              localStore: local,
              dio: Dio()..httpClientAdapter = _okPingAdapter(),
            ),
          ),
        ],
        child: MaterialApp(
          routes: AppRouter.routes,
          // ⚠️ 栈底直接就是发现页 —— 等价于「启动页已被 pushReplacement 顶掉」
          initialRoute: AppRouter.discoveryRoute,
          theme: AppTheme.build(palette: AppPalettes.day),
          builder: (context, child) => AgeScope(
            profile: AgeProfile.of(AgeBand.junior),
            child: child ?? const SizedBox.shrink(),
          ),
        ),
      );
    }

    testWidgets('U4 回归：栈底的发现页连上后必须离场（广播命中，不许黑屏）', (tester) async {
      await tester.pumpWidget(
        bootstrapLikeApp(
          finder: _FakeFinder(
            found: const ServerAddress(host: '192.168.88.19', port: 8810),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // 修复前：栈被弹空 → 这里什么都找不到
      expect(
        find.text('我的学号'),
        findsOneWidget,
        reason: '连上后必须落到登录页（无 token），不能停在空栈上黑屏',
      );
      expect(find.text('扫码连接'), findsNothing, reason: '发现页应当已经离场');
      expect(local.serverHost, '192.168.88.19', reason: '新地址要落到本地，下次启动直接用');
    });

    testWidgets('U4 回归：栈底的发现页手输新地址连上后同样要离场', (tester) async {
      await tester.pumpWidget(
        bootstrapLikeApp(finder: _FakeFinder(supported: false)),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('手动输入地址'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).last, '192.168.88.19');
      await tester.tap(find.text('连接'));
      // ⚠️ 页面会先等 400ms 让对话框退场干净（见 `_overlayExitGuard`），
      //    纯 timer 不会被 `pumpAndSettle` 推进，这里必须显式 elapse。
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      await tester.pumpAndSettle();

      expect(
        find.text('我的学号'),
        findsOneWidget,
        reason: '手输地址连上后必须跳登录页，不许黑屏',
      );
      expect(local.serverHost, '192.168.88.19');
    });
  });

  group('P2 登录页（F14）', () {
    testWidgets('用「我的学号」而不是「用户名」，且密码可显示', (tester) async {
      await tester.pumpWidget(wrap(const LoginPage()));
      await tester.pumpAndSettle();

      expect(find.text('我的学号'), findsOneWidget);
      expect(find.text('我的密码'), findsOneWidget);
      expect(find.text('用户名'), findsNothing);
      expect(find.byIcon(Icons.visibility_off_rounded), findsOneWidget);
      expect(find.text('登 录'), findsOneWidget);

      await tester.tap(find.byIcon(Icons.visibility_off_rounded));
      await tester.pumpAndSettle();
      expect(find.byIcon(Icons.visibility_rounded), findsOneWidget);
    });

    testWidgets('空输入就地提示，不弹模态框（§5.2）', (tester) async {
      await tester.pumpWidget(wrap(const LoginPage()));
      await tester.pumpAndSettle();

      await tester.tap(find.text('登 录'));
      await tester.pumpAndSettle();

      expect(find.text('先写上你的学号吧'), findsOneWidget);
      expect(find.byType(Dialog), findsNothing, reason: '不该用模态框承载错误');
      expect(find.byType(AlertDialog), findsNothing);
    });

    testWidgets('只填学号不填密码也有提示', (tester) async {
      await tester.pumpWidget(wrap(const LoginPage()));
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField).first, 'A20260001');
      await tester.tap(find.text('登 录'));
      await tester.pumpAndSettle();

      expect(find.text('密码还没填哦'), findsOneWidget);
    });
  });

  group('主题切换（U9）', () {
    /// 走真实路径：按钮 → showThemeSwitcher → 底部弹层（这样才有 Material 祖先）。
    Future<void> openSheet(WidgetTester tester) async {
      tester.view.physicalSize = const Size(1080, 2400);
      tester.view.devicePixelRatio = 3.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        wrap(
          Builder(
            builder: (context) => Scaffold(
              body: Center(
                child: TextButton(
                  onPressed: () => showThemeSwitcher(context),
                  child: const Text('打开'),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('打开'));
      await tester.pumpAndSettle();
    }

    testWidgets('四种模式都在，且能切到夜间并记住', (tester) async {
      await openSheet(tester);

      expect(find.text('跟随系统'), findsOneWidget);
      expect(find.text('日间'), findsOneWidget);
      expect(find.text('纸质护眼'), findsOneWidget);
      expect(find.text('夜间'), findsOneWidget);

      await tester.tap(find.text('夜间'));
      await tester.pumpAndSettle();

      final container =
          ProviderScope.containerOf(tester.element(find.byType(ThemeSwitcherSheet)));
      expect(container.read(themeControllerProvider).mode, AppThemeMode.night);
      // 落盘了才算真记住
      final reopened = await LocalStore.open();
      expect(reopened.themeId, 'night');
    });

    testWidgets('自动暖色开关默认开，可关闭', (tester) async {
      await openSheet(tester);

      final switchFinder = find.byType(Switch);
      expect(tester.widget<Switch>(switchFinder).value, isTrue);

      await tester.tap(switchFinder);
      await tester.pumpAndSettle();
      expect(tester.widget<Switch>(switchFinder).value, isFalse);

      final container =
          ProviderScope.containerOf(tester.element(find.byType(ThemeSwitcherSheet)));
      expect(container.read(themeControllerProvider).autoWarm, isFalse);
    });

    testWidgets('切换入口上有预览色块（不只是文字）', (tester) async {
      await openSheet(tester);
      // 每个模式两个圆形预览
      expect(find.byType(Container), findsWidgets);
      final checkIcons = find.byIcon(Icons.check_circle_rounded);
      expect(checkIcons, findsWidgets, reason: '要有"当前选中"的标记');
    });
  });

  group('App 外壳（F3 / F5）', () {
    Future<void> pumpApp(WidgetTester tester, Map<String, Object> prefs, DateTime now) async {
      SharedPreferences.setMockInitialValues(prefs);
      final store = await LocalStore.open();
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            localStoreProvider.overrideWithValue(store),
            broadcastFinderProvider.overrideWithValue(_FakeFinder(supported: false)),
          ],
          child: StudentApp(clock: () => now),
        ),
      );
      await tester.pump();
    }

    testWidgets('夜间模式走 ThemeMode.dark', (tester) async {
      await pumpApp(tester, {'pref.theme': 'night'}, DateTime(2026, 9, 24, 12));

      final app = tester.widget<MaterialApp>(find.byType(MaterialApp));
      expect(app.themeMode, ThemeMode.dark);
      // 切主题必须有 200ms 淡入，否则会闪白（§2.5）
      expect(app.themeAnimationDuration, const Duration(milliseconds: 200));
      // 三套主题都从 theme/darkTheme 走，不靠系统，避免"先白后黑"
      expect(app.darkTheme, isNotNull);

      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('纸质护眼走浅色（白天）', (tester) async {
      await pumpApp(tester, {'pref.theme': 'paper'}, DateTime(2026, 9, 24, 12));

      final app = tester.widget<MaterialApp>(find.byType(MaterialApp));
      expect(app.themeMode, ThemeMode.light);
      // 浅色主题必须是纸质而不是日间
      expect(app.theme?.scaffoldBackgroundColor, AppPalettes.paper.bgColor);

      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('自动暖色优先于手动选择：晚上选了纸质也走夜间（§6.2）', (tester) async {
      await pumpApp(tester, {'pref.theme': 'paper'}, DateTime(2026, 9, 24, 21));

      final app = tester.widget<MaterialApp>(find.byType(MaterialApp));
      expect(app.themeMode, ThemeMode.dark, reason: '护眼优先于功能丰富度');

      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('关掉自动暖色后，晚上仍按手动选择走', (tester) async {
      await pumpApp(
        tester,
        {'pref.theme': 'paper', 'pref.auto_warm': false},
        DateTime(2026, 9, 24, 21),
      );

      final app = tester.widget<MaterialApp>(find.byType(MaterialApp));
      expect(app.themeMode, ThemeMode.light);

      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('分龄档位能在页面里取到（低年级字号最大）', (tester) async {
      late AgeProfile seen;
      await tester.pumpWidget(
        wrap(
          Builder(
            builder: (context) {
              seen = AgeScope.of(context);
              return const SizedBox();
            },
          ),
          band: AgeBand.junior,
        ),
      );
      expect(seen.band, AgeBand.junior);
      expect(seen.body, 18);
    });

    testWidgets('没有 AgeScope 时兜底低年级而不是崩', (tester) async {
      late AgeProfile seen;
      await tester.pumpWidget(
        ProviderScope(
          overrides: [localStoreProvider.overrideWithValue(local)],
          child: MaterialApp(
            home: Builder(
              builder: (context) {
                seen = AgeScope.of(context);
                return const SizedBox();
              },
            ),
          ),
        ),
      );
      expect(seen.band, AgeBand.junior);
    });
  });
}
