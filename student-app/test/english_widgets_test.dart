/// WP-APP-03 英语练习页 —— 组件 / 页面 widget 测试。
///
/// - 纯展示组件（SpellResultCard / TimerBadge / HintButton / KeyboardInputPanel /
///   AnswerInputBar / RecordingIndicator / InlineNotice）：直接 pump，断言渲染与交互。
/// - 练习页（U10 内联无模态 / U13 WS 错误降级键盘）：用 [FakeWsClient] 推 `verdict` /
///   `error` 帧驱动真实 [PracticePage]，断言「内联反馈、不弹模态框」「WS 错误 action=keyboard
///   自动切键盘」。
///
/// 视口设为 1200×3600（默认 800×600 下 ListView 懒加载会让下方控件不构造），
/// 并在 [addTearDown] 里复位。
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/core/net/ws_client.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/widgets/app_button.dart';
import 'package:student_app/core/widgets/feedback.dart';
import 'package:student_app/features/config/practice_session.dart';
import 'package:student_app/features/english/practice_controller.dart';
import 'package:student_app/features/english/practice_page.dart';
import 'package:student_app/features/english/widgets/answer_input_bar.dart';
import 'package:student_app/features/english/widgets/hint_button.dart';
import 'package:student_app/features/english/widgets/keyboard_input_panel.dart';
import 'package:student_app/features/english/widgets/recording_indicator.dart';
import 'package:student_app/features/english/widgets/spell_result_card.dart';
import 'package:student_app/features/english/widgets/timer_badge.dart';

// ---------------------------------------------------------------------------
// 假 WS 客户端（只驱动页面，不连真后端）
// ---------------------------------------------------------------------------

class FakeWsClient extends WsClient {
  FakeWsClient({required super.serverStore, required super.localStore});

  final _ctl = StreamController<Map<String, dynamic>>.broadcast();

  @override
  Stream<Map<String, dynamic>> get messages => _ctl.stream;

  /// 由测试主动推一帧，模拟服务端下发。
  void push(Map<String, dynamic> msg) => _ctl.add(msg);

  @override
  Future<void> connect({int? lastAnswerId}) async {}

  @override
  Future<void> close() async {}
}

class FakePracticeRepository implements PracticeRepository {
  AnswerVerdict nextVerdict =
      const AnswerVerdict(isCorrect: true, matchType: 'exact');
  PracticeSettle nextSettle = const PracticeSettle(
    sessionId: 0,
    totalItems: 0,
    correctItems: 0,
    accuracy: 0,
    score: 0,
    hintCount: 0,
    avgDurationMs: 0,
    durationMs: 0,
    wrongItems: <WrongItem>[],
    improved: false,
  );

  @override
  Future<AnswerVerdict> submitAnswer({
    required int sessionId,
    required int seq,
    required int? wordId,
    required String mode,
    required int step,
    required String asrText,
    required int durationMs,
    required int firstVoiceMs,
    required bool usedKeyboard,
    required int retryCount,
    required int hintLevel,
    required int usedHintCount,
    /// F15 / 契约 §9.2：提交答案必须带幂等键。
    String? idempotencyKey,
    double? confidence,
  }) async =>
      nextVerdict;

  @override
  Future<PracticeSettle> finish({
    required int sessionId,
    required String reason,
  }) async =>
      nextSettle;

  @override
  Future<void> appeal({required int answerId, String? reason}) async {}

  PracticeHint nextHint =
      const PracticeHint(level: 1, hint: '答案共 5 个字母', exhausted: true);
  bool requestHintThrows = false;
  int? requestHintCode;
  final List<Map<String, dynamic>> hintCalls = <Map<String, dynamic>>[];

  @override
  Future<PracticeHint> requestHint({
    required int sessionId,
    required int idx,
    required int wordId,
    required String mode,
    required int currentLevel,
  }) async {
    hintCalls.add(<String, dynamic>{
      'sessionId': sessionId,
      'idx': idx,
      'wordId': wordId,
      'mode': mode,
      'currentLevel': currentLevel,
    });
    if (requestHintThrows) {
      throw ApiException.business(requestHintCode ?? 0, 'err');
    }
    return nextHint;
  }
}

// ---------------------------------------------------------------------------
// 夹具
// ---------------------------------------------------------------------------

PracticeItem wordItem() => PracticeItem(
      seq: 1,
      wordId: 101,
      mode: 'cn2en',
      prompt: '苹果',
      requireSpell: true,
      targetLen: 5,
      exampleCn: 'apple',
    );

PracticeSession sessionOf(List<PracticeItem> items, {int id = 99}) =>
    PracticeSession(sessionId: id, items: items, timeLimitMs: 0);

/// 包裹主题 + 分龄，给展示组件一个能取到 `AppExtraColors`/`AgeScope` 的环境。
/// 用 `Scaffold` 提供 Material 祖先（TextField 等需要）。
Widget wrap(Widget child) => MaterialApp(
      theme: AppTheme.build(palette: AppPalettes.day),
      home: AgeScope(
        profile: AgeProfile.ofGrade(3),
        child: Scaffold(body: child),
      ),
    );

void setBigViewport(WidgetTester tester) {
  tester.view.physicalSize = const Size(1200, 3600);
  addTearDown(tester.view.reset);
}

// ---------------------------------------------------------------------------
// 纯展示组件
// ---------------------------------------------------------------------------

void main() {
  // U2：逐字母回显各态（match / wrong / missing / extra）正确渲染。
  testWidgets('U2 逐字母回显 match/wrong/missing/extra 均渲染', (tester) async {
    setBigViewport(tester);
    final diff = [
      const AnswerDiffCell(pos: 0, expected: 'c', actual: 'c', state: 'match'),
      const AnswerDiffCell(pos: 1, expected: 'l', actual: 'i', state: 'wrong'),
      const AnswerDiffCell(pos: 2, expected: 'a', actual: '', state: 'missing'),
      const AnswerDiffCell(pos: 3, expected: '', actual: 'x', state: 'extra'),
    ];
    await tester.pumpWidget(wrap(SpellResultCard(
      diff: diff,
      rawText: 'see ell eye ess',
    )));
    await tester.pumpAndSettle();

    // 各态的字符都出现（match 的 C / wrong 读成的 I / extra 的 X；
    // SpellResultCard 按契约逐字母大写展示）。
    expect(find.text('C'), findsWidgets);
    expect(find.text('I'), findsWidgets);
    expect(find.text('X'), findsWidgets);
    // 一句话精确到第几位（wrong 在第 2 位）。
    expect(find.textContaining('第 2 位听成了'), findsOneWidget);
    // 原始识别文本展示（双通道：学生理解机器听到了什么）。
    expect(find.textContaining('我听到的是'), findsOneWidget);
  });

  // U12：不限时徽标显示「不限时」。
  testWidgets('U12 不限时徽标显示不限时', (tester) async {
    setBigViewport(tester);
    await tester.pumpWidget(wrap(const TimerBadge(unlimited: true, remainingSeconds: 0)));
    await tester.pumpAndSettle();
    expect(find.text('不限时'), findsOneWidget);
  });

  // U12：剩余 ≤10 秒（这里给 8）变色文案仍正确（不闪烁由组件保证）。
  testWidgets('U12 限时剩余 8 秒显示倒计时', (tester) async {
    setBigViewport(tester);
    await tester.pumpWidget(wrap(const TimerBadge(unlimited: false, remainingSeconds: 8)));
    await tester.pumpAndSettle();
    expect(find.text('8 秒'), findsOneWidget);
  });

  // F12：一档提示展示占位框数量。
  testWidgets('F12 一档提示显示字母数占位', (tester) async {
    setBigViewport(tester);
    await tester.pumpWidget(wrap(const HintButton(
      hintLevel: 1,
      placeholderCount: 5,
      onHint: _noop,
    )));
    await tester.pumpAndSettle();
    expect(find.textContaining('有 5 个字母'), findsOneWidget);
  });

  // WP-2J：二档提示渲染首字母文案，且未用尽时按钮仍可点（可继续升档）。
  testWidgets('WP2J 二档提示渲染首字母且可继续升档', (tester) async {
    setBigViewport(tester);
    await tester.pumpWidget(wrap(HintButton(
      hintLevel: 2,
      placeholderCount: 5,
      onHint: _noop,
      hintContent: '首字母：A',
      exhausted: false,
    )));
    await tester.pumpAndSettle();
    expect(find.text('首字母：A'), findsOneWidget);
    final btn = tester.widget<AppButton>(find.byType(AppButton));
    expect(btn.onPressed, isNotNull, reason: '未用尽必须仍可点');
  });

  // WP-2J：三档提示渲染完整答案，且用尽后按钮置灰、展示「三档提示都已用完」。
  testWidgets('WP2J 三档提示渲染完整答案且用尽禁用', (tester) async {
    setBigViewport(tester);
    await tester.pumpWidget(wrap(HintButton(
      hintLevel: 3,
      placeholderCount: 5,
      onHint: _noop,
      hintContent: 'apple',
      exhausted: true,
    )));
    await tester.pumpAndSettle();
    final btn = tester.widget<AppButton>(find.byType(AppButton));
    expect(btn.onPressed, isNull, reason: '用尽必须真正不可点');
    expect(find.text('apple'), findsOneWidget);
    expect(find.text('三档提示都已用完'), findsOneWidget);
  });

  // WP-2J：二档用尽（失败次数不足升三档）→ 按钮置灰并提示先再试一次。
  testWidgets('WP2J 当前门控用尽后禁用并提示解锁', (tester) async {
    setBigViewport(tester);
    await tester.pumpWidget(wrap(HintButton(
      hintLevel: 2,
      placeholderCount: 5,
      onHint: _noop,
      hintContent: '首字母：A',
      exhausted: true,
    )));
    await tester.pumpAndSettle();
    final btn = tester.widget<AppButton>(find.byType(AppButton));
    expect(btn.onPressed, isNull);
    expect(find.text('当前提示已用完，先再试一次也许能解锁下一档'), findsOneWidget);
  });

  // F8/U7：键盘面板提交回调拿到文本。
  testWidgets('F8 键盘面板提交回传文本', (tester) async {
    setBigViewport(tester);
    var submitted = '';
    await tester.pumpWidget(wrap(KeyboardInputPanel(
      onSubmit: (t) => submitted = t,
      onCancel: _noop,
    )));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'apple');
    await tester.tap(find.text('提交'));
    await tester.pumpAndSettle();
    expect(submitted, 'apple');
  });

  // F8/U7：常驻「改用键盘」入口常弱化且可点。
  testWidgets('F8 常驻改用键盘入口可点', (tester) async {
    setBigViewport(tester);
    var tapped = false;
    await tester.pumpWidget(wrap(AnswerInputBar(onUseKeyboard: () => tapped = true)));
    await tester.pumpAndSettle();
    expect(find.text('改用键盘'), findsOneWidget);
    await tester.tap(find.text('改用键盘'));
    await tester.pumpAndSettle();
    expect(tapped, isTrue);
  });

  // 录音指示器不闪、能构造（呼吸动画用 repeat，不能用 pumpAndSettle）。
  testWidgets('RecordingIndicator 可构造不抛', (tester) async {
    setBigViewport(tester);
    await tester.pumpWidget(wrap(const RecordingIndicator()));
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.byType(RecordingIndicator), findsOneWidget);
  });

  // 内联反馈是就地组件（非 Dialog）。
  testWidgets('InlineNotice 是内联组件而非模态', (tester) async {
    setBigViewport(tester);
    await tester.pumpWidget(wrap(const InlineNotice(
      kind: NoticeKind.error,
      text: '没听清，再说一次',
    )));
    await tester.pumpAndSettle();
    expect(find.byType(InlineNotice), findsOneWidget);
    expect(find.byType(Dialog), findsNothing);
    expect(find.text('没听清，再说一次'), findsOneWidget);
  });

  // U10：答错走内联反馈，绝不弹模态框。
  testWidgets('U10 答错内联反馈，不弹模态框', (tester) async {
    setBigViewport(tester);
    SharedPreferences.setMockInitialValues(<String, Object>{});
    final prefs = await SharedPreferences.getInstance();
    final local = LocalStore(prefs);
    final server = ServerStore(local);
    final fakeWs = FakeWsClient(serverStore: server, localStore: local);
    final fakeRepo = FakePracticeRepository();

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          wsClientProvider.overrideWithValue(fakeWs),
          practiceRepositoryProvider.overrideWithValue(fakeRepo),
        ],
        child: wrap(PracticePage(session: sessionOf([wordItem()]))),
      ),
    );
    await tester.pump(); // 让 initState 注册 WS 监听
    // 推一个错误 verdict 帧。
    fakeWs.push(<String, dynamic>{
      'type': 'verdict',
      'is_correct': false,
      'match_type': 'wrong',
    });
    await tester.pump(const Duration(milliseconds: 50));
    await tester.pump(const Duration(milliseconds: 50));

    // 关键断言：没有模态框，而是就地 InlineNotice。
    expect(find.byType(Dialog), findsNothing);
    expect(find.byType(AlertDialog), findsNothing);
    expect(find.byType(InlineNotice), findsWidgets);
    expect(find.textContaining('再说一次'), findsWidgets);
  });

  // U13：WS error action=keyboard → 自动切键盘（页面出现键盘面板）。
  testWidgets('U13 WS 错误 action=keyboard 自动切键盘', (tester) async {
    setBigViewport(tester);
    SharedPreferences.setMockInitialValues(<String, Object>{});
    final prefs = await SharedPreferences.getInstance();
    final local = LocalStore(prefs);
    final server = ServerStore(local);
    final fakeWs = FakeWsClient(serverStore: server, localStore: local);
    final fakeRepo = FakePracticeRepository();

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          wsClientProvider.overrideWithValue(fakeWs),
          practiceRepositoryProvider.overrideWithValue(fakeRepo),
        ],
        child: wrap(PracticePage(session: sessionOf([wordItem()]))),
      ),
    );
    await tester.pump();
    fakeWs.push(<String, dynamic>{
      'type': 'error',
      'code': 50001,
      'action': 'keyboard',
      'ui_hint': '网络不太顺，换成打字继续吧',
    });
    await tester.pump(const Duration(milliseconds: 50));
    await tester.pump(const Duration(milliseconds: 50));

    // 键盘面板出现（降级路径生效）。
    expect(find.byType(KeyboardInputPanel), findsOneWidget);
  });
}

void _noop() {}
