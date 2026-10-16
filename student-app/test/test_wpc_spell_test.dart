/// WP-C 背诵链路验收 —— 契约 v1.3 交互重构（规格 52 APP-12，U16-1 ~ U16-9）。
///
/// U3   startSpellRun 请求体 {unit_id,type,mode} 与契约一致（spell_session 回归）
/// U16-1 按住 → start 发出、音频推流；松开 → end 发出、推流停止
/// U16-2 按住中 partial/utterance 实时上屏；松开收 verdict(true) → 绿+👍+倒计时+answer
/// U16-3 verdict(false) → 红态、推流停止；此后 partial 不再上屏
/// U16-4 倒计时走完自动下一题（对/错两条路径）；「再来一次」取消倒计时并清空
/// U16-5 跳过 → answer(correct=false) + auto-add + 立即下一题
/// U16-6 键盘屏：逐字母填入/⌫ 回退/填满自动判定上报；语音⇄键盘互切不清内容
/// U16-7 最后一题倒计时走完 → settle → 结算页；无「结束并结算」按钮
/// U16-8 判对不调 DELETE /api/wrongbook（v1.3 废除）
/// U16-9 verdict 超时兜底：end 后 1.5s 无 verdict → 可再来一次，不死锁
/// U7/U8  en2zh 回归：转写比对判对/判错跳过（交互保留，被动适配）
/// U11    结算页停 3 秒自动回单元列表（回归）
/// U12/U13 断线转暂停 / 服务端 error（回归，适配新交互）
library;

import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
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

/// 按需吐帧的录音器：start 返回可控流（测试观察「推流/停推流」）。
class _TapRecorder extends PcmRecorder {
  final StreamController<Uint8List> frames = StreamController<Uint8List>();

  @override
  Future<MicPermission> ensurePermission() async => MicPermission.granted;

  @override
  Future<Stream<Uint8List>> start() async => frames.stream;

  @override
  Future<void> stop() async {}
}

/// WsClient 间谍：记录上行的 JSON 与音频帧数（不真连）。
class SpyWsClient extends WsClient {
  SpyWsClient({required super.serverStore, required super.localStore});

  final sent = <Map<String, dynamic>>[];
  int audioFrames = 0;

  @override
  void sendRawJson(Map<String, dynamic> payload) => sent.add(payload);

  @override
  bool sendAudio(Uint8List chunk) {
    audioFrames += 1;
    return true;
  }

  @override
  void sendEnd() => sent.add({'type': 'end'});
}

/// 会话假件：消息与连接状态都可注入，绝不连真 WS / 不碰麦克风。
class FakeSession {
  FakeSession(this.serverStore, this.local) {
    ws = SpyWsClient(serverStore: serverStore, localStore: local);
    session = PracticeWsSession(
      wsClient: ws,
      micRecorder: recorder,
      serverMessages: messages.stream,
      serverStates: states.stream,
      autoConnect: false,
    );
  }

  final ServerStore serverStore;
  final LocalStore local;

  final recorder = _TapRecorder();
  late final SpyWsClient ws;
  late final PracticeWsSession session;

  final messages = StreamController<Map<String, dynamic>>.broadcast();
  final states = StreamController<WsState>.broadcast();

  /// 服务端 verdict 下行（§5 v1.3）：松开后整词判定结果。
  void sayVerdict({
    required int itemId,
    required bool correct,
    String text = '',
  }) {
    messages.add({
      'type': 'verdict',
      'correct': correct,
      'text': text,
      'session_id': 'ps-1',
      'item_id': itemId,
    });
  }

  /// 服务端 partial 下行：按住中实时回显。
  void sayPartial(String text) =>
      messages.add({'type': 'partial', 'text': text, 'is_final': false});

  /// 服务端 utterance 下行：语句级定版回显。
  void sayUtterance({required int itemId, required String text}) {
    messages.add({
      'type': 'utterance',
      'text': text,
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
    case '/api/settings':
      return okBody({'spell_verdict_countdown_s': 3});
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
    // ⚠️ 带时长推进：进页的 GET /api/settings（dio 零时长 Timer）要走完，
    // 否则测试结束报「Timer is still pending」。
    await tester.pump(const Duration(milliseconds: 10));
    await tester.pump(const Duration(milliseconds: 10));
    return received;
  }

  List<RequestRecord> callsOf(List<RequestRecord> received, String path) =>
      received.where((r) => r.options.uri.path == path).toList();

  /// 事件经「注入流 → session → 页面」两级广播转发，
  /// 一拍 pump 只走完第一级微任务 —— 两拍才稳定上图（探针实测）。
  /// ⚠️ pump 必须带时长：假时钟下 dio 的零时长 Timer 不推进就不触发（见 U3）。
  Future<void> pumpEvents(WidgetTester tester) async {
    await tester.pump(const Duration(milliseconds: 10));
    await tester.pump(const Duration(milliseconds: 10));
  }

  /// 让 answer/auto-add 这类 dio 请求链路走完并排干帧
  /// （verdict 后的倒计时 label 等）。倒计时 periodic Timer 一秒一跳，
  /// pumpAndSettle 在无待渲染帧时即返回，不会等到下一次 tick，安全。
  Future<void> drainHttp(WidgetTester tester) async {
    await tester.pump(const Duration(milliseconds: 20));
    await tester.pumpAndSettle();
  }

  /// 按下大圆钮（按住中）。
  Future<TestGesture> pressHold(WidgetTester tester) async {
    final gesture = await tester.startGesture(
      tester.getCenter(find.byKey(const ValueKey('spell-hold'))),
    );
    await tester.pump();
    return gesture;
  }

  /// 松开大圆钮。
  Future<void> releaseHold(TestGesture gesture, WidgetTester tester) async {
    await gesture.up();
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

  testWidgets('U16-1 按住 → start+音频推流；松开 → end+推流停止', (tester) async {
    final fake = FakeSession(serverStore, local);
    await pumpSpell(tester, run: zh2enRun(items: [appleItem()]), fake: fake);
    addTearDown(fake.dispose);

    // 按下：start 上行（信封 {type, session_id, mode, item_id}）
    final gesture = await pressHold(tester);
    expect(fake.ws.sent.where((m) => m['type'] == 'start'), hasLength(1));
    expect(fake.ws.sent.where((m) => m['type'] == 'start').single, {
      'type': 'start',
      'session_id': 'ps-1',
      'mode': 'spell_zh2en',
      'item_id': 101,
    });

    // 按住期间：音频帧逐包上送
    fake.recorder.frames.add(Uint8List.fromList(List.filled(4800, 1)));
    await pumpEvents(tester);
    expect(fake.ws.audioFrames, greaterThanOrEqualTo(1));

    // 松开：end 上行；此后帧不再推流（press-and-hold 门控）
    await releaseHold(gesture, tester);
    expect(fake.ws.sent.where((m) => m['type'] == 'end'), hasLength(1));
    final framesAtRelease = fake.ws.audioFrames;
    fake.recorder.frames.add(Uint8List.fromList(List.filled(4800, 2)));
    await pumpEvents(tester);
    expect(fake.ws.audioFrames, framesAtRelease,
        reason: '松开后必须停推流（§5：音频帧只在按住期间推）');

    // 收尾排干：verdict 1.5s 超时 Timer + 超时 snack（3s）
    await tester.pump(const Duration(milliseconds: 1500));
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
  });

  testWidgets('U16-2 partial/utterance 实时上屏；verdict(true) → 绿+👍+倒计时+answer',
      (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(
      tester,
      run: zh2enRun(items: [appleItem()]),
      fake: fake,
    );
    addTearDown(fake.dispose);

    final gesture = await pressHold(tester);

    // 按住中 partial 实时上屏（浅色进行态，未定版也显示；方案 B 逐格填色）
    fake.sayPartial('a');
    await pumpEvents(tester);
    expect(find.text('A'), findsOneWidget, reason: 'partial 实时转大写填格');

    // 语句级定版：收进累积，partial 不重复
    fake.sayUtterance(itemId: 101, text: 'a p');
    await pumpEvents(tester);
    expect(find.text('P'), findsOneWidget, reason: 'utterance 定版字母填格');

    // 松开 → 服务端 verdict 判对
    await releaseHold(gesture, tester);
    fake.sayVerdict(itemId: 101, correct: true, text: 'a p p l e');
    await drainHttp(tester);

    // 判对：全格绿 + 👍（判对反馈）
    expect(find.text('👍'), findsOneWidget);
    expect(find.text('E'), findsOneWidget, reason: '判定文本定版填满 5 格');
    // 倒计时启动：「再来一次 (3)」
    expect(find.text('再来一次 (3)'), findsOneWidget);

    // answer 留痕：correct 必填（契约 §4.1 v1.3）
    final answerCalls = callsOf(received, '/api/practice/answer');
    expect(answerCalls, hasLength(1));
    expect(answerCalls.single.options.data, {
      'session_id': 'ps-1',
      'item_id': 101,
      'mode': 'spell_zh2en',
      'answer': 'a p p l e',
      'elapsed_ms': isA<int>(),
      'correct': true,
    });

    // 收尾排干：倒计时走完 → 最后一题自动 settle → 结算页 3s 回跳
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('UNITS'), findsOneWidget);
  });

  testWidgets('U16-3 verdict(false) → 红态；此后 partial 不再上屏', (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(
      tester,
      run: zh2enRun(items: [appleItem()]),
      fake: fake,
    );
    addTearDown(fake.dispose);

    final gesture = await pressHold(tester);
    fake.sayPartial('a p p l z');
    await pumpEvents(tester);
    expect(find.text('Z'), findsOneWidget, reason: 'partial 逐格填色');

    await releaseHold(gesture, tester);
    fake.sayVerdict(itemId: 101, correct: false, text: 'a p p l z');
    await drainHttp(tester);

    // 判错：内容定版（norm 去空格连字符），对齐着色出「再来一次」
    // apple vs applz：A/P/P/L 对齐绿，Z 对齐到 E 的位置=红
    expect(find.text('Z'), findsOneWidget);
    expect(find.text('再来一次 (3)'), findsOneWidget,
        reason: '判错同样进入倒计时（F16-4）');

    // 此后到达的 partial 不再上屏（U16-3）
    fake.sayPartial('x x');
    await pumpEvents(tester);
    expect(find.text('Z'), findsOneWidget);
    expect(find.text('X'), findsNothing,
        reason: '判错后说出的拼读内容丢弃不显示');

    // answer 留痕 correct=false
    final answerCalls = callsOf(received, '/api/practice/answer');
    expect(answerCalls.single.options.data['correct'], isFalse);

    // 收尾排干：判错倒计时走完 → auto-add + settle → 结算页回跳
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('UNITS'), findsOneWidget);
  });

  testWidgets('U16-4 倒计时走完自动下一题（对/错）；「再来一次」取消倒计时清空识别区',
      (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(tester, run: zh2enRun(), fake: fake);
    addTearDown(fake.dispose);

    // 第 1 题判对 → 倒计时 → 自动下一题
    var gesture = await pressHold(tester);
    await releaseHold(gesture, tester);
    fake.sayVerdict(itemId: 101, correct: true, text: 'apple');
    await drainHttp(tester);
    expect(find.text('香蕉'), findsNothing);

    await tester.pump(const Duration(seconds: 3));
    await tester.pump();
    expect(find.text('香蕉'), findsOneWidget, reason: '判对倒计时走完自动下一题');
    expect(callsOf(received, '/api/wrongbook/auto-add'), isEmpty,
        reason: '判对不入错题本');

    // 第 2 题判错 → 「再来一次」取消倒计时并清空识别区（不限次）
    gesture = await pressHold(tester);
    await releaseHold(gesture, tester);
    fake.sayVerdict(itemId: 102, correct: false, text: 'bananas');
    await drainHttp(tester);
    // banana vs bananas：6 格全对齐绿 + 多读的 S 附尾红格
    expect(find.text('S'), findsOneWidget);

    await tester.tap(find.text('再来一次 (3)'));
    await tester.pump();
    expect(find.text('S'), findsNothing, reason: '清空识别区');

    // 倒计时已取消：再等 3 秒仍在本题
    await tester.pump(const Duration(seconds: 3));
    await tester.pump();
    expect(find.text('香蕉'), findsOneWidget, reason: '取消倒计时不推进');

    // 重新按住再来一轮（不限次）→ 判错 → 倒计时走完先 auto-add 再推进（最后一题→settle）
    gesture = await pressHold(tester);
    await releaseHold(gesture, tester);
    fake.sayVerdict(itemId: 102, correct: false, text: 'bananas');
    await drainHttp(tester);
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.text('背完啦'), findsOneWidget);
    final autoAdds = callsOf(received, '/api/wrongbook/auto-add');
    expect(autoAdds, hasLength(1));
    expect(autoAdds.single.options.data, {'item_id': 102, 'mode': 'spell_zh2en'});
  });

  testWidgets('U16-5 跳过 → answer(correct=false) + auto-add + 立即下一题',
      (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(tester, run: zh2enRun(), fake: fake);
    addTearDown(fake.dispose);

    // 未按住直接跳过（常驻按钮，任何时刻可点）
    await tester.tap(find.text('跳过（判错）'));
    await tester.pumpAndSettle();

    final answerCalls = callsOf(received, '/api/practice/answer');
    expect(answerCalls, hasLength(1));
    expect(answerCalls.single.options.data, {
      'session_id': 'ps-1',
      'item_id': 101,
      'mode': 'spell_zh2en',
      'answer': '',
      'elapsed_ms': isA<int>(),
      'correct': false,
    });

    final autoAdds = callsOf(received, '/api/wrongbook/auto-add');
    expect(autoAdds, hasLength(1));
    expect(autoAdds.single.options.data, {'item_id': 101, 'mode': 'spell_zh2en'});

    expect(find.text('香蕉'), findsOneWidget, reason: '跳过立即下一题');
  });

  testWidgets('U16-6 键盘屏：逐字母填入/⌫ 回退/填满自动判定；互切不清内容',
      (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(
      tester,
      run: zh2enRun(items: [appleItem()]),
      fake: fake,
    );
    addTearDown(fake.dispose);

    // 切到键盘屏（图标互切）
    await tester.tap(find.byIcon(Icons.keyboard_rounded));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('spell-kbd-input')), findsOneWidget);

    // 逐字母填入（不满不判）
    await tester.enterText(find.byKey(const ValueKey('spell-kbd-input')), 'APPL');
    await tester.pumpAndSettle();
    expect(callsOf(received, '/api/practice/answer'), isEmpty,
        reason: '未填满不判定');

    // ⌫ 逐字母回退
    await tester.sendKeyEvent(LogicalKeyboardKey.backspace);
    await tester.pumpAndSettle();
    final field = tester.widget<TextField>(
      find.byKey(const ValueKey('spell-kbd-input')),
    );
    expect(field.controller!.text, 'APP', reason: '⌫ 逐字母回退');

    // 补齐填满（小写输入验证忽略大小写）→ answer(correct=true) + 倒计时
    await tester.enterText(find.byKey(const ValueKey('spell-kbd-input')), 'apple');
    // ⚠️ 填满即判定并上报 answer：drainHttp 让 POST 走完并启动倒计时
    await drainHttp(tester);
    final answerCalls = callsOf(received, '/api/practice/answer');
    expect(answerCalls, hasLength(1));
    expect(answerCalls.single.options.data, {
      'session_id': 'ps-1',
      'item_id': 101,
      'mode': 'spell_zh2en',
      'answer': 'APPLE',
      'elapsed_ms': isA<int>(),
      'correct': true,
    });
    expect(find.text('再来一次 (3)'), findsOneWidget, reason: '键盘判定同走 F16-4 倒计时');

    // 收尾排干：倒计时 → settle → 结算页回跳
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('UNITS'), findsOneWidget);
  });

  testWidgets('U16-6b 语音⇄键盘互切不清已填内容（A6）', (tester) async {
    final fake = FakeSession(serverStore, local);
    await pumpSpell(
      tester,
      run: zh2enRun(items: [appleItem()]),
      fake: fake,
    );
    addTearDown(fake.dispose);

    // 语音轮：判错后内容定版
    final gesture = await pressHold(tester);
    fake.sayUtterance(itemId: 101, text: 'a p');
    await pumpEvents(tester);
    await releaseHold(gesture, tester);
    fake.sayVerdict(itemId: 101, correct: false, text: 'a p');
    await drainHttp(tester);
    // 方案 B 逐格：A/P 对齐绿，apple 后 3 格红空框
    expect(find.text('A'), findsOneWidget);
    expect(find.text('P'), findsOneWidget);

    // 切键盘：内容带进输入框
    await tester.tap(find.byIcon(Icons.keyboard_rounded));
    await tester.pumpAndSettle();
    final field = tester.widget<TextField>(
      find.byKey(const ValueKey('spell-kbd-input')),
    );
    expect(field.controller!.text, 'AP', reason: '语音→键盘互切不清内容');

    // 切回语音：内容仍在识别区
    await tester.tap(find.byIcon(Icons.mic_rounded));
    await tester.pumpAndSettle();
    expect(find.text('A'), findsOneWidget, reason: '键盘→语音互切不清内容');
    expect(find.text('P'), findsOneWidget, reason: '键盘→语音互切不清内容');

    // 收尾排干：判错倒计时走完 → settle → 结算页回跳
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('UNITS'), findsOneWidget);
  });

  testWidgets('U16-7 最后一题倒计时走完 → settle → 结算页；无「结束并结算」',
      (tester) async {
    final fake = FakeSession(serverStore, local);
    // unit_ids/types 传本轮快照（F16-7，契约 §4.1）
    final received = await pumpSpell(
      tester,
      run: SpellRun(
        sessionId: 'ps-1',
        mode: 'spell_zh2en',
        unitName: 'Starter Unit 1',
        typeName: 'word',
        items: [appleItem()],
        startedAt: DateTime.now(),
        unitIds: const [7],
        types: const ['word'],
      ),
      fake: fake,
    );
    addTearDown(fake.dispose);

    expect(find.text('结束并结算'), findsNothing,
        reason: '「结束并结算」按钮已删除（决策 #14）');

    final gesture = await pressHold(tester);
    await releaseHold(gesture, tester);
    fake.sayVerdict(itemId: 101, correct: true, text: 'apple');
    await drainHttp(tester);

    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    final settleCalls = callsOf(received, '/api/practice/settle');
    expect(settleCalls, hasLength(1), reason: '最后一题倒计时走完自动 settle');
    expect(settleCalls.single.options.data, {
      'session_id': 'ps-1',
      'duration_ms': isA<int>(),
      'unit_ids': [7],
      'types': ['word'],
    });
    expect(find.text('背完啦'), findsOneWidget);

    // 结算页 3 秒回跳计时器走完（测试结束不得有挂起 Timer）
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('UNITS'), findsOneWidget);
  });

  testWidgets('U16-8 判对不调 DELETE /api/wrongbook（v1.3 废除）', (tester) async {
    final fake = FakeSession(serverStore, local);
    final received = await pumpSpell(
      tester,
      run: zh2enRun(items: [appleItem()]),
      fake: fake,
    );
    addTearDown(fake.dispose);

    final gesture = await pressHold(tester);
    await releaseHold(gesture, tester);
    fake.sayVerdict(itemId: 101, correct: true, text: 'apple');
    await drainHttp(tester);
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('背完啦'), findsOneWidget);

    final deletes = received.where(
      (r) =>
          r.options.method == 'DELETE' &&
          r.options.uri.path.contains('/api/wrongbook'),
    );
    expect(deletes, isEmpty, reason: '判对不再自动移出错题本（服务端写 mastered_at）');

    // 收尾排干：结算页 3s 回跳 Timer
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('UNITS'), findsOneWidget);
  });

  testWidgets('U16-9 verdict 超时兜底：end 后 1.5s 无 verdict → 可再来一次，不死锁',
      (tester) async {
    final fake = FakeSession(serverStore, local);
    await pumpSpell(
      tester,
      run: zh2enRun(items: [appleItem()]),
      fake: fake,
    );
    addTearDown(fake.dispose);

    final gesture = await pressHold(tester);
    await releaseHold(gesture, tester);
    expect(find.text('松开结束'), findsNothing);

    // 1.5s 无 verdict → 按「本轮未判定」处理：回到可按住状态
    await tester.pump(const Duration(milliseconds: 1500));
    await tester.pump();
    expect(find.text('按住拼读'), findsOneWidget, reason: '超时后不死锁，可重新按住');

    // 超时 snack 3 秒后自动消失（避免挂起 Timer）
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    // 再来一次：重新按住 + verdict 判对 → 正常推进
    final gesture2 = await pressHold(tester);
    await releaseHold(gesture2, tester);
    fake.sayVerdict(itemId: 101, correct: true, text: 'apple');
    await drainHttp(tester);
    expect(find.text('👍'), findsOneWidget, reason: '判对全格绿 + 👍');
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('背完啦'), findsOneWidget);

    // 收尾排干：结算页 3s 回跳 Timer
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('UNITS'), findsOneWidget);
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

    await tester.tap(find.text('开始说'));
    await tester.pumpAndSettle();
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

    await tester.tap(find.text('开始说'));
    await tester.pumpAndSettle();
    fake.sayTranscript('难过');
    await tester.pumpAndSettle();

    expect(find.textContaining('再试一次'), findsWidgets);
    expect(find.text('跳过（判错）'), findsOneWidget);
    // 判错不中断：还停在本题
    expect(find.text('happy'), findsOneWidget);

    // 跳过 → 计入错误并推进
    await tester.tap(find.text('跳过（判错）'));
    await tester.pumpAndSettle();
    expect(find.text('sad'), findsOneWidget);

    final answerCalls = callsOf(received, '/api/practice/answer');
    expect(answerCalls, hasLength(1), reason: '服务端已判过，跳过不重复上报');
    expect(answerCalls.single.options.data['answer'], '难过',
        reason: 'en2zh 只转发转写文本，correct 由服务端判定');

    final autoAdds = callsOf(received, '/api/wrongbook/auto-add');
    expect(autoAdds, hasLength(1));
    expect(autoAdds.single.options.data, {'item_id': 201, 'mode': 'spell_en2zh'});
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

  testWidgets('U12 断线转暂停：进度不清、可重新按住继续', (tester) async {
    final fake = FakeSession(serverStore, local);
    await pumpSpell(
      tester,
      run: zh2enRun(items: [appleItem()]),
      fake: fake,
    );
    addTearDown(fake.dispose);

    // 按住中听到了内容
    await pressHold(tester);
    fake.sayPartial('a');
    await pumpEvents(tester);
    expect(find.text('A'), findsOneWidget);

    // 断线 → 暂停提示，进度不清（状态流也是广播转发，双拍）
    fake.states.add(WsState.closed);
    await pumpEvents(tester);
    expect(find.textContaining('连接断开了'), findsOneWidget);
    expect(find.text('A'), findsOneWidget, reason: '断线不清作答进度（§5）');
    expect(find.byKey(const ValueKey('spell-hold')), findsOneWidget,
        reason: '断线后可重新按住继续');
  });

  testWidgets('U13 服务端 error（start 被拒）：报错可见、进度不清、可继续', (tester) async {
    final fake = FakeSession(serverStore, local);
    await pumpSpell(
      tester,
      run: zh2enRun(items: [appleItem()]),
      fake: fake,
    );
    addTearDown(fake.dispose);

    await pressHold(tester);
    fake.sayPartial('a');
    await pumpEvents(tester);
    expect(find.text('A'), findsOneWidget);

    // 服务端 abort（2026-10-11 真机：mode 被拒后界面无反馈成死页）
    fake.messages.add({
      'type': 'error',
      'code': 10001,
      'message': '语音没识别出来',
      'action': 'abort',
    });
    await pumpEvents(tester);
    expect(find.textContaining('语音没识别出来'), findsOneWidget,
        reason: '报错必须可见，不能无声卡死');
    expect(find.text('A'), findsOneWidget, reason: '报错不清作答进度');
    expect(find.byKey(const ValueKey('spell-hold')), findsOneWidget,
        reason: '报错后按钮要还给用户');
  });
}
