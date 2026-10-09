/// WP-C 背诵链路验收 —— U3 ~ U12（30-学生端 WP-C 验收用例）。
///
/// U3  start 请求体 {unit_id,type,mode} 与契约一致
/// U4  Mock WS letter 序列填格：正确字母按 index 填格、错误不填
/// U5  填满判对 → 自动下一题，answer 上报 correct=true
/// U6  3 连错出键盘入口；键盘填格同样判对/判错上报
/// U7  en2zh：转写「开心的」 vs zh=「高兴的」alt=「开心、快乐」→ 服务端判对
/// U8  en2zh 判错：显示再试一次，可跳过且计入错误
/// U9  settle 请求与结算页：correct+wrong=total；三项数字正确
/// U10 错题 auto-add：mode 与当次模式一致
/// U11 3 秒自动回跳：无挂起 Timer
/// U12 断线转暂停：进度不清、可继续
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/audio/pcm_recorder.dart';
import 'package:student_app/core/net/practice_ws.dart';
import 'package:student_app/core/net/ws_client.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/features/home/practice_entry.dart';
import 'package:student_app/features/recite/settle_page.dart';
import 'package:student_app/features/recite/spell_page.dart';
import 'package:student_app/features/recite/spell_session.dart';
import 'package:student_app/router/routes/home.dart';
import 'package:student_app/router/routes/recite.dart';

import 'support/fake_home_repository.dart';

/// 永不真启动的录音器：权限放行、start 给空流（测试不碰系统录音插件）。
class _NeverRecorder extends PcmRecorder {
  @override
  Future<MicPermission> ensurePermission() async => MicPermission.granted;

  @override
  Future<Stream<Uint8List>> start() async => const Stream<Uint8List>.empty();

  @override
  Future<void> stop() async {}
}

/// 会话假件：消息与连接状态都可注入，绝不连真 WS / 不碰麦克风。
class FakeSession {
  FakeSession(this.serverStore, this.local) {
    session = PracticeWsSession(
      wsClient: WsClient(serverStore: serverStore, localStore: local),
      micRecorder: _NeverRecorder(),
      serverMessages: messages.stream,
      serverStates: states.stream,
      autoConnect: false,
    );
  }

  final ServerStore serverStore;
  final LocalStore local;

  final messages = StreamController<Map<String, dynamic>>.broadcast();
  final states = StreamController<WsState>.broadcast();
  late final PracticeWsSession session;

  void sayLetter({
    required int itemId,
    required int index,
    required String letter,
    required bool correct,
  }) {
    messages.add({
      'type': 'letter',
      'index': index,
      'letter': letter,
      'correct': correct,
      'session_id': 'ps-1',
      'item_id': itemId,
    });
  }

  void sayTranscript(String text, {bool isFinal = true}) {
    messages.add({'type': isFinal ? 'final' : 'partial', 'text': text});
  }

  Future<void> dispose() async {
    await messages.close();
    await states.close();
    await session.close();
  }
}

/// 一次被捕获的 HTTP 请求（断言请求体用）。
class RequestRecord {
  RequestRecord(this.options);

  final RequestOptions options;
}

String defaultRespond(RequestOptions options) {
  switch (options.uri.path) {
    case '/api/practice/answer':
      return okBody({'correct': true});
    case '/api/practice/settle':
      return okBody(<String, dynamic>{});
    case '/api/wrongbook/auto-add':
      return okBody({'item_id': 1, 'mode': 'spell_zh2en'});
    default:
      return okBody(null);
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    SharedPreferences.setMockInitialValues({
      StoreKeys.serverHost: '127.0.0.1',
      StoreKeys.serverPort: 8810,
    });
    local = await LocalStore.open();
    serverStore = ServerStore(local);
  });

  SpellItem appleItem() =>
      const SpellItem(id: 101, en: 'apple', pos: 'n.', zh: '苹果', altZh: '');

  SpellItem bananaItem() =>
      const SpellItem(id: 102, en: 'banana', pos: 'n.', zh: '香蕉', altZh: '');

  SpellItem happyItem() => const SpellItem(
        id: 201,
        en: 'happy',
        pos: 'adj.',
        zh: '高兴的',
        altZh: '开心、快乐',
      );

  SpellItem sadItem() =>
      const SpellItem(id: 202, en: 'sad', pos: 'adj.', zh: '难过的', altZh: '');

  SpellRun zh2enRun({List<SpellItem>? items}) => SpellRun(
        sessionId: 'ps-1',
        mode: 'spell_zh2en',
        unitName: 'Starter Unit 1',
        typeName: 'word',
        items: items ?? [appleItem(), bananaItem()],
        startedAt: DateTime.now(),
      );

  SpellRun en2zhRun({List<SpellItem>? items}) => SpellRun(
        sessionId: 'ps-2',
        mode: 'spell_en2zh',
        unitName: 'Starter Unit 1',
        typeName: 'word',
        items: items ?? [happyItem(), sadItem()],
        startedAt: DateTime.now(),
      );

  Widget bootSpell({
    required SpellRun run,
    required FakeSession fake,
    required JsonAdapter adapter,
  }) {
    return ProviderScope(
      overrides: [
        localStoreProvider.overrideWithValue(local),
        apiClientProvider.overrideWithValue(
          clientWith(adapter, serverStore: serverStore, localStore: local),
        ),
      ],
      child: MaterialApp(
        theme: AppTheme.build(palette: AppPalettes.day),
        builder: (context, child) => AgeScope(
          profile: AgeProfile.of(AgeBand.junior),
          child: child!,
        ),
        routes: {
          HomeRoutes.home: (_) => const Scaffold(body: Text('HOME')),
          HomeRoutes.unitList: (_) => const Scaffold(body: Text('UNITS')),
          ReciteRoutes.spell: (_) => SpellPage(run: run, session: fake.session),
          ReciteRoutes.settle: (_) => const SettlePage(),
        },
        home: SpellPage(run: run, session: fake.session),
      ),
    );
  }

  Future<List<RequestRecord>> pumpSpell(
    WidgetTester tester, {
    required SpellRun run,
    required FakeSession fake,
    String Function(RequestOptions options) respond = defaultRespond,
  }) async {
    final received = <RequestRecord>[];
    final adapter = JsonAdapter((options) {
      received.add(RequestRecord(options));
      return respond(options);
    });
    await tester.pumpWidget(bootSpell(run: run, fake: fake, adapter: adapter));
    await tester.pump();
    return received;
  }

  List<RequestRecord> callsOf(List<RequestRecord> received, String path) =>
      received.where((r) => r.options.uri.path == path).toList();

  /// letter 事件经「注入流 → session → 页面」两级广播转发，
  /// 一拍 pump 只走完第一级微任务 —— 两拍才稳定上图（探针实测）。
  Future<void> pumpLetter(WidgetTester tester) async {
    await tester.pump();
    await tester.pump();
  }

  testWidgets('U3 startSpellRun 发出的 start 请求体与契约一致', (tester) async {
    final received = <RequestOptions>[];
    final adapter = JsonAdapter((options) {
      received.add(options);
      return okBody({
        'session_id': 'ps-1',
        'items': [
          {'id': 101, 'en': 'apple', 'pos': 'n.', 'zh': '苹果', 'alt_zh': ''}
        ],
      });
    });
    final api =
        clientWith(adapter, serverStore: serverStore, localStore: local);

    SpellRun? run;
    unawaited(
      startSpellRun(
        api,
        selection: const PracticeSelection(
            unitId: 7, unitName: 'U7', type: 'word'),
        mode: 'spell_zh2en',
      ).then((v) => run = v),
    );
    // dio 内部有零时长 Timer（假时钟下 pump 才会推进它）
    for (var i = 0; i < 10 && run == null; i++) {
      await tester.pump(const Duration(milliseconds: 10));
    }
    expect(run, isNotNull, reason: 'startSpellRun 应在推进时钟后完成');

    expect(received, hasLength(1));
    expect(received.single.uri.path, '/api/practice/start');
    expect(received.single.data, {
      'unit_id': 7,
      'type': 'word',
      'mode': 'spell_zh2en',
    });
    expect(run!.sessionId, 'ps-1');
    expect(run!.items.single.en, 'apple');
  });

  testWidgets('U4 Mock WS letter 序列填格：正确按 index 填、错误不填', (tester) async {
    final fake = FakeSession(serverStore, local);
    await pumpSpell(
      tester,
      run: zh2enRun(items: [appleItem()]),
      fake: fake,
    );
    addTearDown(fake.dispose);

    fake.sayLetter(itemId: 101, index: 0, letter: 'A', correct: true);
    await pumpLetter(tester);
    expect(find.text('A'), findsOneWidget);

    fake.sayLetter(itemId: 101, index: 1, letter: 'X', correct: false);
    await pumpLetter(tester);
    // 错误不填格
    expect(find.text('X'), findsNothing);
    // 其他格仍空（只填了第 0 格）
    expect(find.text('P'), findsNothing);
  });

  testWidgets('U5 填满判对 → answer(correct=true) → 立即切下一题', (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(
      tester,
      run: zh2enRun(),
      fake: fake,
    );
    addTearDown(fake.dispose);

    final letters = 'apple'.split('');
    for (var i = 0; i < letters.length; i++) {
      fake.sayLetter(
          itemId: 101, index: i, letter: letters[i], correct: true);
      await pumpLetter(tester);
    }
    await tester.pumpAndSettle();

    // answer 上报：本地判格 correct=true，answer=拼出的字母串
    final answerCalls = callsOf(received, '/api/practice/answer');
    expect(answerCalls, hasLength(1));
    expect(answerCalls.single.options.data, {
      'session_id': 'ps-1',
      'item_id': 101,
      'mode': 'spell_zh2en',
      'answer': 'apple',
      'elapsed_ms': isA<int>(),
      'correct': true, // §6.4：zh2en 本地判格 correct 必填
    });

    // 立即切下一题（无需点按）
    expect(find.text('香蕉'), findsOneWidget);
    expect(find.textContaining('2 / 2'), findsOneWidget);
  });

  testWidgets('U6 同格 3 连错出键盘入口；键盘填错上报判错、跳过推进', (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(
      tester,
      run: zh2enRun(),
      fake: fake,
    );
    addTearDown(fake.dispose);

    // 先开始拼读（真实流程：先按开始，服务端才判）
    await tester.tap(find.text('开始拼读'));
    await tester.pumpAndSettle();

    // 同一格 3 次 correct=false → 出键盘入口
    for (var i = 0; i < 3; i++) {
      fake.sayLetter(itemId: 101, index: 1, letter: 'x', correct: false);
      await pumpLetter(tester);
    }
    expect(find.text('清空重拼'), findsOneWidget,
        reason: '同一格 3 连错必须出现键盘入口（§6.4）');

    // 键盘填错：A P P L Z ≠ apple → 上报判错、留在本题
    for (final letter in ['A', 'P', 'P', 'L', 'Z']) {
      await tester.tap(find.text(letter).last);
      await tester.pump();
    }
    await tester.pumpAndSettle();

    final answerCalls = callsOf(received, '/api/practice/answer');
    expect(answerCalls, hasLength(1));
    expect(answerCalls.single.options.data['correct'], isFalse);
    expect(answerCalls.single.options.data['answer'], 'APPLZ');
    expect(find.text('苹果'), findsOneWidget, reason: '键盘判错后留在本题');

    // 3 次连错（WS 阶段）→ 可跳过；本题已上报过，跳过不再重复上报
    await tester.tap(find.text('跳过这题（判错）'));
    await tester.pumpAndSettle();

    expect(callsOf(received, '/api/practice/answer'), hasLength(1),
        reason: '已判过的题跳过不重复上报');
    expect(find.text('香蕉'), findsOneWidget);
  });

  testWidgets('U7 en2zh：转写「开心的」命中别名 → 服务端判对 → 自动下一题', (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(
      tester,
      run: en2zhRun(),
      fake: fake,
      respond: (options) {
        if (options.uri.path == '/api/practice/answer') {
          final answer = (options.data as Map)['answer'] as String;
          // 服务端 compare_service 语义：归一化 + 别名表（开心=高兴的）
          final correct = answer == '开心的';
          return okBody({'correct': correct, 'matched_zh': '高兴的'});
        }
        return defaultRespond(options);
      },
    );
    addTearDown(fake.dispose);

    expect(find.text('happy'), findsOneWidget);
    expect(find.text('adj.'), findsOneWidget);

    fake.sayTranscript('开心的');
    await tester.pumpAndSettle();

    final answerCalls = callsOf(received, '/api/practice/answer');
    expect(answerCalls, hasLength(1));
    expect(answerCalls.single.options.data, {
      'session_id': 'ps-2',
      'item_id': 201,
      'mode': 'spell_en2zh',
      'answer': '开心的',
      'elapsed_ms': isA<int>(),
    });
    // 判对 → 立即下一题
    expect(find.text('sad'), findsOneWidget);
  });

  testWidgets('U8 en2zh 判错：显示再试一次，可跳过且计入错误', (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(
      tester,
      run: en2zhRun(),
      fake: fake,
      respond: (options) {
        if (options.uri.path == '/api/practice/answer') {
          return okBody({'correct': false});
        }
        return defaultRespond(options);
      },
    );
    addTearDown(fake.dispose);

    fake.sayTranscript('难过');
    await tester.pumpAndSettle();

    expect(find.textContaining('再试一次'), findsWidgets);
    expect(find.text('跳过这题（算错）'), findsOneWidget);
    // 判错不中断：还停在本题
    expect(find.text('happy'), findsOneWidget);

    // 跳过 → 计入错误并推进
    await tester.tap(find.text('跳过这题（算错）'));
    await tester.pumpAndSettle();
    expect(find.text('sad'), findsOneWidget);

    final answerCalls = callsOf(received, '/api/practice/answer');
    expect(answerCalls, hasLength(1), reason: '服务端已判过，跳过不重复上报');
    expect(answerCalls.single.options.data['answer'], '难过',
        reason: 'en2zh 只转发转写文本，correct 由服务端判定');
  });

  testWidgets('U9+U10 走完全场 → settle + 结算页三项数字 + auto-add mode 一致',
      (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(
      tester,
      run: zh2enRun(),
      fake: fake,
    );
    addTearDown(fake.dispose);

    // 第 1 题拼对
    final letters = 'apple'.split('');
    for (var i = 0; i < letters.length; i++) {
      fake.sayLetter(
          itemId: 101, index: i, letter: letters[i], correct: true);
      await pumpLetter(tester);
    }
    await tester.pumpAndSettle();

    // 第 2 题：zh2en 换题即自动重发 start 开始新一轮拼读（无需点按）
    for (var i = 0; i < 3; i++) {
      fake.sayLetter(itemId: 102, index: 0, letter: 'z', correct: false);
      await pumpLetter(tester);
    }
    await tester.tap(find.text('跳过这题（判错）'));
    await tester.pumpAndSettle();

    // settle 请求
    final settleCalls = callsOf(received, '/api/practice/settle');
    expect(settleCalls, hasLength(1));
    expect(settleCalls.single.options.data, {
      'session_id': 'ps-1',
      'duration_ms': isA<int>(),
    });

    // 结算页三项数字：正确 1 · 错误 1 · 已记入错题 1（1+1=2=total ✓）
    expect(find.text('正确'), findsOneWidget);
    expect(find.text('错误'), findsOneWidget);
    expect(find.text('已记入错题'), findsOneWidget);
    expect(find.text('1'), findsNWidgets(3));

    // U10：判错的题 auto-add，mode=当次模式
    final autoAdds = callsOf(received, '/api/wrongbook/auto-add');
    expect(autoAdds, hasLength(1));
    expect(autoAdds.single.options.data, {
      'item_id': 102,
      'mode': 'spell_zh2en',
    });

    // 结算页的 3 秒回跳计时器也要走完（测试结束时不得有挂起 Timer）
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('UNITS'), findsOneWidget);
  });

  testWidgets('U11 结算页停 3 秒自动回单元列表；无挂起 Timer', (tester) async {
    const summary = SettleSummary(
      correct: 3,
      wrong: 1,
      wrongAdded: 1,
      mode: 'spell_zh2en',
      unitName: 'Starter Unit 1',
      typeName: 'word',
    );
    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.build(palette: AppPalettes.day),
      builder: (context, child) => AgeScope(
        profile: AgeProfile.of(AgeBand.junior),
        child: child!,
      ),
      routes: {
        HomeRoutes.home: (_) => const Scaffold(body: Text('HOME')),
        HomeRoutes.unitList: (_) => const Scaffold(body: Text('UNITS')),
        ReciteRoutes.settle: (_) => SettlePage(summary: summary),
      },
      initialRoute: ReciteRoutes.settle,
    ));
    await tester.pump();

    expect(find.text('正确'), findsOneWidget);
    expect(find.text('3'), findsOneWidget);
    expect(find.text('错误'), findsOneWidget);
    expect(find.text('1'), findsNWidgets(2)); // 错误 1 + 已记入错题 1

    // 3 秒计时器：先 pump 推进触发回跳，再 pumpAndSettle 排干帧
    // （pumpAndSettle 只等帧不等 Timer，直接调用会在 Timer 未触发时提前返回）
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('UNITS'), findsOneWidget);
    expect(find.text('背完啦'), findsNothing);
  });

  testWidgets('U12 断线转暂停：进度不清、出现「继续」', (tester) async {
    final fake = FakeSession(serverStore, local);
    await pumpSpell(
      tester,
      run: zh2enRun(items: [appleItem()]),
      fake: fake,
    );
    addTearDown(fake.dispose);

    // 先拼出 1 格
    fake.sayLetter(itemId: 101, index: 0, letter: 'A', correct: true);
    await pumpLetter(tester);
    expect(find.text('A'), findsOneWidget);

    // 断线 → 暂停提示，进度不清（状态流也是广播转发，双拍）
    fake.states.add(WsState.closed);
    await pumpLetter(tester);
    expect(find.textContaining('连接断开了'), findsOneWidget);
    expect(find.text('A'), findsOneWidget, reason: '断线不清作答进度（§5）');
    expect(find.text('继续'), findsOneWidget, reason: '重连后可从「继续」接着来');
  });
}
