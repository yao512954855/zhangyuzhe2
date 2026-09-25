/// WP-APP-05 「我的」/记录/退出登录/只练错题 —— U1 / U3 / F1 / F2 / F7 / F9。
///
/// 说明（重要）：本文件只依赖「干净模块」——
/// `MyRecordsPage`（`mine_page`/`wrong_book_page` 因复用 `practice_launcher`
/// → `practice.dart` → 未落地的 `practice_page.dart`(APP-03) 而卷入编译链路，
/// 故"错题本页面渲染"类用例见交付说明偏离项 D-APP03，本文件用生产代码层
/// （`PracticeConfig.toRequest`）兜底验证 F1「只练错题 scope=wrong」。
///
/// - U1 / F1：只练错题的请求体 `strategy.scope` 必须为 `wrong`（生产代码层断言）；
/// - U3：我的记录渲染**真能拿到**的进度汇总；
/// - F2：绝不伪造场次列表/单次详情入口；
/// - F7：退出登录清登录态、保留服务器地址、回登录页；
/// - F9：一期不做游戏化（无星星/排行/打卡/积分/勋章）。
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/widgets/app_button.dart';
import 'package:student_app/features/config/practice_strategy.dart';
import 'package:student_app/features/home/models.dart';
import 'package:student_app/features/mine/my_records_page.dart';

import 'test_app05_helpers.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    (local, serverStore) = await openTestStore();
  });

  group('U1 / F1 只练错题：复用生产组卷，scope=wrong 且语文不发 mode', () {
    test('英语只练错题 → strategy.scope == "wrong"', () {
      final config = PracticeConfig(
        unit: const UnitItem(
          unitId: 11,
          subject: Subject.english,
          name: 'Unit 1',
          wordTotal: 24,
          withError: 5,
        ),
        scope: PracticeScope.wrong,
      );
      final body = config.toRequest();
      final strategy = (body['strategy'] as Map).cast<String, dynamic>();
      expect(strategy['scope'], 'wrong');
      expect(body['content_type'], 'english');
    });

    test('语文练习请求体绝对不含 mode（红线：语文不发 mode）', () {
      final config = PracticeConfig(
        unit: const UnitItem(
          unitId: 12,
          subject: Subject.chinese,
          name: '第一单元',
          textTotal: 3,
        ),
        scope: PracticeScope.unit,
      );
      final body = config.toRequest();
      final strategy = (body['strategy'] as Map).cast<String, dynamic>();
      expect(strategy.containsKey('mode'), isFalse, reason: '语文请求体不得含 mode');
    });
  });

  group('U3 我的记录：渲染真实进度汇总', () {
    testWidgets('今日/连续/已掌握/正确率/累计都来自 /api/me/progress', (tester) async {
      useTallScreen(tester);
      final adapter = defaultAdapter(
        progress: progressJson(streakDays: 7, masteredWords: 50, accuracy: 0.85, practicedWords: 80, totalWords: 120),
      );
      await tester.pumpWidget(bootApp(const MyRecordsPage(), local: local, serverStore: serverStore, adapter: adapter));
      await tester.pumpAndSettle();

      expect(find.text('今日练习'), findsOneWidget);
      expect(find.text('连续练习(天)'), findsOneWidget);
      expect(find.text('已掌握词条'), findsOneWidget);
      expect(find.text('正确率'), findsOneWidget);
      expect(find.text('7'), findsWidgets);
      expect(find.text('50'), findsWidgets);
      expect(find.text('85%'), findsOneWidget);
      expect(find.text('累计练习 80/120 个英语词条'), findsOneWidget);
    });

    testWidgets('进度接口报错时给中文错误而非崩溃', (tester) async {
      useTallScreen(tester);
      final adapter = JsonAdapter((options) {
        if (options.uri.path == '/api/me/progress') {
          return '{"code":10001,"message":"登录已过期","data":null,"trace_id":"t"}';
        }
        return okBody(const {});
      });
      await tester.pumpWidget(bootApp(const MyRecordsPage(), local: local, serverStore: serverStore, adapter: adapter));
      await tester.pumpAndSettle();

      expect(find.textContaining('登录已过期'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });

  group('F2 不伪造场次列表/单次详情', () {
    testWidgets('入口只有 我的统计/设置/关于，没有"练习记录"列表入口', (tester) async {
      useTallScreen(tester);
      final adapter = defaultAdapter();
      await tester.pumpWidget(bootApp(const MyRecordsPage(), local: local, serverStore: serverStore, adapter: adapter));
      await tester.pumpAndSettle();

      expect(find.text('我的统计'), findsOneWidget);
      expect(find.text('设置'), findsOneWidget);
      expect(find.text('关于'), findsOneWidget);
      expect(find.textContaining('查看全部'), findsNothing);
      expect(find.textContaining('练习记录'), findsNothing);
    });
  });

  group('F7 退出登录：清登录态、保留服务器地址、回登录页', () {
    testWidgets('确认退出后 token 清空、服务器地址保留、跳登录页', (tester) async {
      useTallScreen(tester);
      final adapter = defaultAdapter();
      final spy = PushSpy();
      await tester.pumpWidget(bootApp(const MyRecordsPage(), local: local, serverStore: serverStore, adapter: adapter, observers: [spy]));
      await tester.pumpAndSettle();

      await tester.tap(find.widgetWithText(AppButton, '退出登录'));
      await tester.pumpAndSettle();
      expect(find.text('退出登录'), findsWidgets);
      await tester.tap(find.text('退出'));
      await tester.pumpAndSettle();

      expect(local.token, isNull);
      expect(local.serverHost, kTestServerHost);
      expect(local.serverPort, kTestServerPort);
      expect(spy.pushedNames, contains('/login'));
    });
  });

  group('F9 一期不做游戏化', () {
    testWidgets('我的记录页没有星星/排行/打卡/积分/勋章', (tester) async {
      useTallScreen(tester);
      final adapter = defaultAdapter();
      await tester.pumpWidget(bootApp(const MyRecordsPage(), local: local, serverStore: serverStore, adapter: adapter));
      await tester.pumpAndSettle();
      for (final forbidden in const ['星星', '排行', '打卡', '积分', '勋章']) {
        expect(find.text(forbidden), findsNothing, reason: '不应出现游戏化元素「$forbidden」');
      }
    });
  });
}
