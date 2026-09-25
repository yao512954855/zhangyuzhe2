/// APP-02 首页与引导页 —— U1 / U2 / U5 / U10 / U11 + F4 / F6 的界面部分。
///
/// 用**假仓库**（`implements HomeRepository`）而不是真网络：
/// 这些用例关心的是"数据长这样时界面怎么画"，把网络层换成"直接返回固定数据"
/// 才能让失败信息指向真正的责任人。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/router/app_router.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/core/widgets/app_button.dart';
import 'package:student_app/features/home/home_controller.dart';
import 'package:student_app/features/home/home_repository.dart';
import 'package:student_app/features/home/main_shell.dart';
import 'package:student_app/features/home/models.dart';

import 'support/fake_home_repository.dart';

UnitsPage pageWith(List<Map<String, dynamic>> items) =>
    UnitsPage.fromJson({'grade': 3, 'semester': 'first', 'items': items});

Map<String, dynamic> englishUnitJson({
  int id = 11,
  String name = 'Unit 1 Hello!',
  int total = 24,
  int practiced = 20,
  int mastered = 14,
  String status = 'practiced_with_error',
}) =>
    {
      'unit_id': id,
      'subject': 'english',
      'name': name,
      'word_total': total,
      'practiced': practiced,
      'mastered': mastered,
      'accuracy': 0.88,
      'status': status,
    };

Map<String, dynamic> chineseUnitJson({
  int id = 12,
  String name = '第一单元',
  int total = 3,
  int recited = 1,
}) =>
    {
      'unit_id': id,
      'subject': 'chinese',
      'name': name,
      'text_total': total,
      'recited': recited,
      'last_grade': '良好',
      'status': 'practiced_ok',
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    local = await LocalStore.open();
  });

  /// 挂上真路由表（首页会 pushNamed 到单元详情 / 配置页）。
  ///
  /// ⚠️ **不能用 `initialRoute: '/home'`**：Flutter 会把 initialRoute 按 `/`
  /// 逐级拆开，连 `/` 上的启动页一起 build。而启动页的异步流程
  /// （探活失败 → `pushReplacement` 到服务发现页）会把栈顶的首页**顶掉**，
  /// 测试里的表现是"第 2 帧首页还在、第 3 帧变成了服务器发现页"，
  /// 顺带留下一个真 UDP 广播的定时器让 `pumpAndSettle` 永远等不完。
  /// 正确做法：`home:` 直接给被测页面，并把 `/` 从 routes 里摘掉。
  Widget boot({required HomeRepository repo, AgeBand band = AgeBand.junior}) {
    final routes = Map<String, WidgetBuilder>.from(AppRouter.routes)
      ..remove(AppRouter.initialRoute);
    return ProviderScope(
      overrides: [
        localStoreProvider.overrideWithValue(local),
        homeRepositoryProvider.overrideWithValue(repo),
        broadcastFinderProvider.overrideWithValue(SilentBroadcastFinder()),
      ],
      child: MaterialApp(
        home: const MainShell(),
        routes: routes,
        theme: AppTheme.build(palette: AppPalettes.day),
        builder: (context, child) => AgeScope(
          profile: AgeProfile.of(band),
          child: child ?? const SizedBox.shrink(),
        ),
      ),
    );
  }

  group('路由接管（F18 协议的验收点）', () {
    test('APP-02 已接管首页：/home 上有真页面，临时落地页不再被指向', () {
      expect(AppRouter.homeIsReal, isTrue);
      expect(AppRouter.homeRoute, '/home');
      expect(AppRouter.routes.containsKey('/home'), isTrue);
      expect(AppRouter.routes.containsKey('/home/unit'), isTrue);
      expect(AppRouter.routes.containsKey('/home/grade'), isTrue);
      expect(AppRouter.routes.containsKey('/home/config'), isTrue);
      // 临时页仍注册着（APP-01 的测试在用它），但已不是登录后的落地目标
      expect(AppRouter.routes.containsKey('/__home_placeholder'), isTrue);
    });
  });

  group('U1 首次登录强制选年级学期', () {
    testWidgets('没选过年级 → 整页是引导页，且**没有返回按钮**', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(boot(repo: FakeHomeRepository()));
      await tester.pumpAndSettle();

      expect(find.text('先选一下年级'), findsOneWidget);
      expect(find.text('你是几年级的？'), findsOneWidget);
      expect(find.text('现在是哪个学期？'), findsOneWidget);
      expect(find.text('好了，开始学'), findsOneWidget);

      // 强制引导靠"结构上没有上一步"来保证，而不是靠拦返回键
      expect(find.byType(BackButton), findsNothing);
      expect(find.byTooltip('返回'), findsNothing);
    });

    testWidgets('1–9 年级都能选（九宫格），未选年级时主按钮不可点', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(boot(repo: FakeHomeRepository()));
      await tester.pumpAndSettle();

      for (var grade = 1; grade <= 9; grade++) {
        expect(find.text('$grade 年级'), findsOneWidget);
      }

      final button = tester.widget<AppButton>(
        find.widgetWithText(AppButton, '好了，开始学'),
      );
      expect(button.onPressed, isNull, reason: '没选年级时不该能提交');

      await tester.tap(find.text('3 年级'));
      await tester.pumpAndSettle();

      final enabled = tester.widget<AppButton>(
        find.widgetWithText(AppButton, '好了，开始学'),
      );
      expect(enabled.onPressed, isNotNull);
    });

    testWidgets('选完年级 → 自动进入首页（不需要额外跳转）', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(
        boot(
          repo: FakeHomeRepository(
            units: pageWith([englishUnitJson()]),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('3 年级'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('上学期'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('好了，开始学'));
      await tester.pumpAndSettle();

      // 引导页消失、首页出现
      expect(find.text('你是几年级的？'), findsNothing);
      // 单元卡标题带学科前缀（UnitCard 里是「${subject.label} ${name}」）
      expect(find.text('英语 Unit 1 Hello!'), findsOneWidget);
      expect(local.grade, 3);
      expect(local.semester, isNotNull);
    });
  });

  group('U2 二次登录不再询问', () {
    testWidgets('本地已有年级学期 → 直接进首页，不出现引导页', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 4, semester: 2);

      await tester.pumpWidget(
        boot(repo: FakeHomeRepository(units: pageWith([englishUnitJson()]))),
      );
      await tester.pumpAndSettle();

      expect(find.text('你是几年级的？'), findsNothing);
      expect(find.text('英语 Unit 1 Hello!'), findsOneWidget);
      // 顶部要显示上次的选择（F2：长期记忆）
      expect(find.text('4 年级 · 下学期'), findsOneWidget);
    });
  });

  group('U5 / F5 单元卡', () {
    testWidgets('四种掌握状态各有各的图标（不只靠颜色区分）', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(
          repo: FakeHomeRepository(
            units: pageWith([
              englishUnitJson(id: 1, name: 'U1', status: 'not_started'),
              englishUnitJson(id: 2, name: 'U2', status: 'practiced_with_error'),
              englishUnitJson(id: 3, name: 'U3', status: 'practiced_ok'),
              englishUnitJson(id: 4, name: 'U4', status: 'mastered'),
            ]),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.radio_button_unchecked_rounded), findsWidgets);
      expect(find.byIcon(Icons.error_outline_rounded), findsWidgets);
      expect(find.byIcon(Icons.check_circle_outline_rounded), findsWidgets);
      expect(find.byIcon(Icons.star_rounded), findsWidgets);
    });

    testWidgets('卡片显示数字型进度与正确率（P4 要点）', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(repo: FakeHomeRepository(units: pageWith([englishUnitJson()]))),
      );
      await tester.pumpAndSettle();

      expect(find.text('20/24'), findsOneWidget);
      expect(find.text('24 个词'), findsOneWidget);
      expect(find.text('正确率 88%'), findsOneWidget);
    });

    testWidgets('每张卡都有「开始练习」（F6 快捷开始，不用先进配置页）', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(repo: FakeHomeRepository(units: pageWith([englishUnitJson()]))),
      );
      await tester.pumpAndSettle();

      expect(find.text('开始练习'), findsOneWidget);
    });
  });

  group('U11 语文卡显示篇数而不是词数', () {
    testWidgets('语文单元显示「1/3 篇」「3 篇课文」，且不出现「个词」', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(repo: FakeHomeRepository(units: pageWith([chineseUnitJson()]))),
      );
      await tester.pumpAndSettle();

      // 默认 Tab 是英语 → 先切到语文（F4）
      await tester.tap(find.text('语文'));
      await tester.pumpAndSettle();

      expect(find.text('1/3 篇'), findsOneWidget);
      expect(find.text('3 篇课文'), findsOneWidget);
      expect(find.textContaining('个词'), findsNothing);
      expect(find.textContaining('最近一篇：良好'), findsOneWidget);
    });
  });

  group('F4 学科切换（纯本地过滤，不重新请求）', () {
    testWidgets('切到语文只看语文单元，切回英语还能看到原来的', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(
          repo: FakeHomeRepository(
            units: pageWith([
              englishUnitJson(name: 'Unit 1 Hello!'),
              chineseUnitJson(name: '第一单元'),
            ]),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('英语 Unit 1 Hello!'), findsOneWidget);
      expect(find.text('语文 第一单元'), findsNothing);

      await tester.tap(find.text('语文'));
      await tester.pumpAndSettle();
      expect(find.text('语文 第一单元'), findsOneWidget);
      expect(find.text('英语 Unit 1 Hello!'), findsNothing);

      await tester.tap(find.text('英语'));
      await tester.pumpAndSettle();
      expect(find.text('英语 Unit 1 Hello!'), findsOneWidget);
    });
  });

  group('F1 顶部进度概要', () {
    testWidgets('显示已练 / 已掌握 / 连续天数', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(
          repo: FakeHomeRepository(
            units: pageWith([englishUnitJson()]),
            progress: const ProgressOverview(
              practicedWords: 180,
              masteredWords: 96,
              streakDays: 5,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('180'), findsOneWidget);
      expect(find.text('已练词'), findsOneWidget);
      expect(find.text('96'), findsOneWidget);
      expect(find.text('5'), findsOneWidget);
      expect(find.text('连续天数'), findsOneWidget);
      expect(find.text('已背篇'), findsNothing);
    });

    testWidgets('切到语文 → 概要跟着换成课文维度（F4 不是只换列表）', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(
          repo: FakeHomeRepository(
            units: pageWith([englishUnitJson(), chineseUnitJson()]),
            progress: const ProgressOverview(
              practicedWords: 180,
              masteredWords: 96,
              totalTexts: 3,
              recitedTexts: 1,
              streakDays: 5,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('语文'));
      await tester.pumpAndSettle();

      // 语文 Tab 上不该出现词条维度的数字：学生看到「已练词 180」只会困惑
      expect(find.text('已练词'), findsNothing);
      expect(find.text('已掌握'), findsNothing);
      expect(find.text('已背篇'), findsOneWidget);
      expect(find.text('1/3'), findsOneWidget);
      expect(find.text('连续天数'), findsOneWidget);
      expect(find.text('5'), findsOneWidget);
    });
  });

  group('U10 断网不崩', () {
    testWidgets('拉不到数据且没有缓存 → 显示中文错误页 + 重试按钮，不白屏不红屏', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(
          repo: FakeHomeRepository(failUnitsWith: ApiException.network('failed')),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('再试一次'), findsOneWidget);
      // F19：不出现红屏 / 英文异常
      expect(tester.takeException(), isNull);
      final texts = tester
          .widgetList<Text>(find.byType(Text))
          .map((t) => t.data ?? '')
          .join(' | ');
      expect(texts.contains('Exception'), isFalse);
      expect(texts.contains('SocketException'), isFalse);
    });
  });

  group('「只练错题」常驻卡（P4）', () {
    testWidgets('有错题时显示数量并可点', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(repo: FakeHomeRepository(units: pageWith([englishUnitJson()]), wrongCount: 12)),
      );
      await tester.pumpAndSettle();

      expect(find.text('只练错题（12 个）'), findsOneWidget);
      expect(find.text('把做错过的词再练一遍'), findsOneWidget);
    });

    testWidgets('没错题时说明原因，不显示误导性的数字', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(repo: FakeHomeRepository(units: pageWith([englishUnitJson()]), wrongCount: 0)),
      );
      await tester.pumpAndSettle();

      expect(find.text('只练错题'), findsOneWidget);
      expect(find.text('还没有错题，先正常练几遍吧'), findsOneWidget);
    });

    testWidgets('语文 Tab 显示**语文自己的**错字数，不拿英语的数字充数', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(
          repo: FakeHomeRepository(
            units: pageWith([englishUnitJson(), chineseUnitJson()]),
            wrongCount: 12,
            wrongCharsCount: 7,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('语文'));
      await tester.pumpAndSettle();

      // 两个端点两个口径（§8.8 英语是「词」、§8.10 语文是「字」）——
      // 把英语的「12 个」挂到语文 Tab 下就是假数据。
      expect(find.text('只练错题（12 个）'), findsNothing);
      expect(find.text('错题本（7 个字）'), findsOneWidget);
      expect(find.text('漏背过的字，点进去重背那几篇'), findsOneWidget);

      // ⚠️ 文案是「错题本」不是「只练错题」：点进去是错题本页（按课文分组、
      //    再「重背这篇」），背诵没有"只背这几个字"这种模式，别骗学生。
      expect(find.textContaining('只练错题'), findsNothing);
    });

    testWidgets('语文还没漏背过字 → 如实说，不给一个编出来的数字', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(
          repo: FakeHomeRepository(
            units: pageWith([englishUnitJson(), chineseUnitJson()]),
            wrongCount: 12,
            wrongCharsCount: 0,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('语文'));
      await tester.pumpAndSettle();

      // ⚠️ 别断 `find.text('错题本')` —— 底部 Tab 标签**也叫**「错题本」，
      //    会命中两个。断 hint（唯一）+ 「没有数字就不编」更有意义。
      expect(find.text('还没有漏背的字，先按课文背几篇吧'), findsOneWidget);
      expect(find.textContaining('个字'), findsNothing,
          reason: '一个错字都没有时，不能编一个数字出来');
    });

    testWidgets('语文点错题卡**不走英语那条「先选单元」的路**', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(
          repo: FakeHomeRepository(
            units: pageWith([englishUnitJson(), chineseUnitJson()]),
            wrongCount: 12,
            wrongCharsCount: 7,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('语文'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('错题本（7 个字）'));
      // 语文进的是错题本页（会去拉 §8.10），不是配置页那条 `scope=wrong`。
      // 这里只用固定 pump：真页面首屏在拉数据，加载动画会让 pumpAndSettle 等不完。
      for (var i = 0; i < 4; i++) {
        await tester.pump(const Duration(milliseconds: 200));
      }
      expect(find.text('练哪个单元的错题？'), findsNothing, reason: '语文不该先弹单元选择');
    });
  });

  group('空内容时给明确引导', () {
    testWidgets('这个年级没有内容 → 说明原因，而不是一个空白列表', (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(boot(repo: FakeHomeRepository(units: pageWith(const []))));
      await tester.pumpAndSettle();

      expect(find.text('这个年级还没有英语内容'), findsOneWidget);
      expect(find.textContaining('让管理者先导入'), findsOneWidget);
    });
  });

  group('底部导航（P4）', () {
    testWidgets('三个 Tab 都在；错题本 Tab 已由 APP-05 接管（是「我的」页，不是占位）',
        (tester) async {
      useTallScreen(tester);
      await local.setGradeSemester(grade: 3, semester: 1);

      await tester.pumpWidget(
        boot(repo: FakeHomeRepository(units: pageWith([englishUnitJson()]))),
      );
      await tester.pumpAndSettle();

      expect(find.text('练习'), findsOneWidget);
      expect(find.text('错题本'), findsWidgets);
      expect(find.text('我的'), findsOneWidget);

      await tester.tap(find.text('错题本').last);
      // ⚠️ 本条原为「错题本 / 我的还没落地时给出占位」的时序断言。APP-05 落地后
      //    按协议翻转：`MainShell._registeredPage(MistakesRoutes.mistakes)` 现在能
      //    拿到真页面，`_ComingSoonPage` 不再出现。
      //    这里用固定 pump 而不用 `pumpAndSettle`：真页面首屏会去拉进度，
      //    加载期可能有持续动画，`pumpAndSettle` 会等不到静止。
      for (var i = 0; i < 4; i++) {
        await tester.pump(const Duration(milliseconds: 200));
      }
      expect(find.text('错题本还在做'), findsNothing, reason: '占位页应当已经消失');
      expect(find.text('练错的词、漏背的字，都能在这儿重练'), findsOneWidget,
          reason: '真「我的」页的错题本入口');
    });
  });
}
