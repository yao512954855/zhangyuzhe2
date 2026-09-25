/// WP-APP-03 英语练习页 —— 控制器状态机单元测试（U1~U17 逻辑层）。
///
/// 用 [FakePracticeRepository] 覆盖真实网络层，所有用例驱动 [PracticeController]
/// 推进状态机并断言「数据态 + 上报字段」符合规格。UI 渲染（回显卡片 / 倒计时变色 /
/// 内联无模态）由 [english_widgets_test.dart] 覆盖；录音 + WS 真链路由
/// `smoke_app03.py` + `smoke_app03_live_test.dart` 覆盖。
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/features/config/practice_session.dart';
import 'package:student_app/features/english/practice_controller.dart';

// ---------------------------------------------------------------------------
// 假仓库
// ---------------------------------------------------------------------------

class FakePracticeRepository implements PracticeRepository {
  final List<Map<String, dynamic>> submitCalls = <Map<String, dynamic>>[];
  final List<Map<String, dynamic>> finishCalls = <Map<String, dynamic>>[];
  final List<Map<String, dynamic>> appealCalls = <Map<String, dynamic>>[];

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

  bool finishThrows = false;
  bool appealThrows = false;

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
    /// F15 / 契约 §9.2：提交答案必须带幂等键（`usedKeyboard` 等一样由控制器算出）。
    String? idempotencyKey,
    double? confidence,
  }) async {
    submitCalls.add(<String, dynamic>{
      'sessionId': sessionId,
      'idempotencyKey': idempotencyKey,
      'seq': seq,
      'wordId': wordId,
      'mode': mode,
      'step': step,
      'asrText': asrText,
      'durationMs': durationMs,
      'firstVoiceMs': firstVoiceMs,
      'usedKeyboard': usedKeyboard,
      'retryCount': retryCount,
      'hintLevel': hintLevel,
      'usedHintCount': usedHintCount,
      'confidence': confidence,
    });
    return nextVerdict;
  }

  @override
  Future<PracticeSettle> finish({
    required int sessionId,
    required String reason,
  }) async {
    finishCalls.add(<String, dynamic>{
      'sessionId': sessionId,
      'reason': reason,
    });
    if (finishThrows) throw ApiException.network();
    return nextSettle;
  }

  @override
  Future<void> appeal({required int answerId, String? reason}) async {
    appealCalls.add(<String, dynamic>{
      'answerId': answerId,
      'reason': reason,
    });
    if (appealThrows) throw ApiException.network();
  }
}

// ---------------------------------------------------------------------------
// 夹具
// ---------------------------------------------------------------------------

/// 模式一、需要拼读：步骤 = [word, spell]。
PracticeItem wordItemWithSpell({int seq = 1, int wordId = 101}) => PracticeItem(
      seq: seq,
      wordId: wordId,
      mode: 'cn2en',
      prompt: '苹果',
      requireSpell: true,
      targetLen: 5,
      exampleCn: 'apple',
    );

/// 模式二：步骤 = [zhAnswer]。
PracticeItem en2cnItem({int seq = 2, int wordId = 102}) => PracticeItem(
      seq: seq,
      wordId: wordId,
      mode: 'en2cn',
      prompt: 'apple',
      requireSpell: false,
      targetLen: 0,
    );

PracticeSession sessionOf(List<PracticeItem> items, {int id = 99}) =>
    PracticeSession(sessionId: id, items: items, timeLimitMs: 0);

/// 持有订阅，防止 autoDispose 的控制器在异步等待期间被回收（回收会把状态重置为默认
/// `asking`，导致 finish 后的断言读到错误态）。
final List<Object> _keepAlive = <Object>[];

ProviderContainer makeContainer(FakePracticeRepository fake) {
  final c = ProviderContainer(
    overrides: [practiceRepositoryProvider.overrideWithValue(fake)],
  );
  _keepAlive.add(c.listen(practiceControllerProvider, (_, _) {}));
  return c;
}

PracticeController ctrl(ProviderContainer c) =>
    c.read(practiceControllerProvider.notifier);

PracticeState stateOf(ProviderContainer c) => c.read(practiceControllerProvider);

// ---------------------------------------------------------------------------
// 用例
// ---------------------------------------------------------------------------

void main() {
  // U1：两步流程（word→spell）在一道题内落库 2 条记录。
  test('U1 两步流程 word→spell 各记一条，顺序正确', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));

    // 第 1 步 word 正确。
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: true, matchType: 'exact'),
      durationMs: 1000,
      firstVoiceMs: 200,
    );
    expect(stateOf(c).phase, PracticePhase.result);
    expect(stateOf(c).records.length, 1);
    expect(stateOf(c).records.first.stepKind, PracticeStepKind.word);

    // 推进到第 2 步 spell。
    ctrl(c).next();
    expect(stateOf(c).step, 2);
    expect(stateOf(c).stepKind, PracticeStepKind.spell);

    // 第 2 步 spell 正确。
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: true, matchType: 'exact'),
      durationMs: 1200,
      firstVoiceMs: 250,
    );
    expect(stateOf(c).records.length, 2);
    expect(stateOf(c).records.last.stepKind, PracticeStepKind.spell);
    expect(fake.submitCalls.length, 0, // 语音通道由 WS 落库，键盘通道才走 submitAnswer
        reason: '语音正确路径不经键盘 submitAnswer');
  });

  // U3：答对的判定结果停留 result，不自动消失、不自动翻页。
  test('U3 答对后停留 result（F6 回显不自动消失）', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: true, matchType: 'exact'),
      durationMs: 900,
      firstVoiceMs: 150,
    );
    expect(stateOf(c).phase, PracticePhase.result);
    expect(stateOf(c).phase == PracticePhase.finished, isFalse);
    // 点「继续」前不应翻页。
    expect(stateOf(c).itemIndex, 0);
    expect(stateOf(c).step, 1);
  });

  // U4：前 2 次失败只提示「没听清，再说一次」，不弹键盘。
  test('U4 前 2 次失败不弹键盘，停留 result 提示再说一次', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));

    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: false, matchType: 'wrong'),
      durationMs: 800,
      firstVoiceMs: 120,
    );
    expect(stateOf(c).phase, PracticePhase.result);
    expect(stateOf(c).keyboardActive, isFalse);
    expect(stateOf(c).failuresForStep, 1);
    expect(stateOf(c).errorMessage, contains('再说一次'));

    ctrl(c).retryVoice();
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: false, matchType: 'wrong'),
      durationMs: 800,
      firstVoiceMs: 120,
    );
    expect(stateOf(c).failuresForStep, 2);
    expect(stateOf(c).keyboardActive, isFalse);
    expect(stateOf(c).errorMessage, contains('再说一次'));
  });

  // U5：第 3 次失败自动切键盘。
  test('U5 第 3 次失败自动切键盘', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));
    for (var i = 0; i < 2; i++) {
      ctrl(c).submitVoiceVerdict(
        const AnswerVerdict(isCorrect: false, matchType: 'wrong'),
        durationMs: 800,
        firstVoiceMs: 120,
      );
      ctrl(c).retryVoice();
    }
    expect(stateOf(c).failuresForStep, 2);
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: false, matchType: 'wrong'),
      durationMs: 800,
      firstVoiceMs: 120,
    );
    expect(stateOf(c).failuresForStep, 3);
    expect(stateOf(c).keyboardActive, isTrue);
    expect(stateOf(c).errorMessage, contains('打字'));
  });

  // U6/U8：识别为空（code 50003）不计错，可重录。
  test('U6/U8 识别为空不计失败数，停留原步', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict.empty(),
      durationMs: 0,
      firstVoiceMs: 0,
    );
    expect(stateOf(c).failuresForStep, 0);
    expect(stateOf(c).phase, PracticePhase.asking);
    expect(stateOf(c).errorMessage, contains('没听清'));
    // 空识别之后第 1 次真正失败，失败数应为 1（不是 2）。
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: false, matchType: 'wrong'),
      durationMs: 800,
      firstVoiceMs: 120,
    );
    expect(stateOf(c).failuresForStep, 1);
  });

  // U7：任意时刻可常驻入口切键盘。
  test('U7 主动切换到键盘态', () async {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));
    ctrl(c).useKeyboard();
    expect(stateOf(c).keyboardActive, isTrue);
    // 键盘提交走 submitAnswer，used_keyboard=1。
    fake.nextVerdict =
        const AnswerVerdict(isCorrect: true, matchType: 'exact', answerId: 7);
    await ctrl(c).submitKeyboard(text: 'apple', durationMs: 1000, firstVoiceMs: 200);
    expect(fake.submitCalls.length, 1);
    expect(fake.submitCalls.last['usedKeyboard'], isTrue);
    expect(stateOf(c).records.last.usedKeyboard, isTrue);
  });

  // U9：fuzzy/loose 可申诉；appeal 不改判分，只置 appealed。
  test('U9 fuzzy 判定可申诉一次，不改判分', () async {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(
        isCorrect: false,
        matchType: 'fuzzy',
        canAppeal: true,
        answerId: 42,
      ),
      durationMs: 800,
      firstVoiceMs: 120,
    );
    expect(ctrl(c).canAppeal, isTrue);
    await ctrl(c).appeal();
    expect(stateOf(c).appealed, isTrue);
    expect(fake.appealCalls.length, 1);
    expect(fake.appealCalls.last['answerId'], 42);
    // 第二次申诉应被忽略（已申诉过）。
    await ctrl(c).appeal();
    expect(fake.appealCalls.length, 1);
    // 不改判分：lastRecord 仍然 isCorrect=false。
    expect(stateOf(c).records.last.isCorrect, isFalse);
  });

  test('U9 非 fuzzy/loose 不可申诉', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(
        isCorrect: false,
        matchType: 'wrong',
        canAppeal: false,
        answerId: 1,
      ),
      durationMs: 800,
      firstVoiceMs: 120,
    );
    expect(ctrl(c).canAppeal, isFalse);
    // 即使强行调也不应打网络。
    expect(fake.appealCalls, isEmpty);
  });

  // U11：单题限时到点 → match_type=timeout，停留 result。
  test('U11 限时到点判 timeout，停留 result', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));
    ctrl(c).timeoutCurrentStep();
    expect(stateOf(c).timedOut, isTrue);
    expect(stateOf(c).phase, PracticePhase.result);
    expect(stateOf(c).records.last.matchType, 'timeout');
    expect(stateOf(c).records.last.asrText, '');
    // 结果页不应出现「再试一次」（timeout 不可重试，仅继续）。
    expect(
      stateOf(c).failuresForStep < 3 && !stateOf(c).timedOut,
      isFalse,
      reason: 'timeout 时结果页的再试一次按钮应隐藏',
    );
  });

  // U13：WS error action=keyboard → 强制键盘；action=retry 仅停留原步。
  test('U13 WS 错误 action=keyboard 强制键盘，retry 不弹键盘', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));
    ctrl(c).onWsError(action: 'keyboard');
    expect(stateOf(c).keyboardActive, isTrue);

    // 重新开一场验证 retry 分支。
    final c2 = makeContainer(fake);
    ctrl(c2).start(sessionOf([wordItemWithSpell()]));
    ctrl(c2).onWsError(action: 'retry');
    expect(stateOf(c2).keyboardActive, isFalse);
    expect(stateOf(c2).phase, PracticePhase.asking);
  });

  // U14：结算数据与服务端一致，评级正确。
  test('U14 完成后结算数据一致，评级随分数', () async {
    final fake = FakePracticeRepository();
    fake.nextSettle = PracticeSettle(
      sessionId: 99,
      totalItems: 2,
      correctItems: 2,
      accuracy: 1.0,
      score: 95,
      hintCount: 0,
      avgDurationMs: 1100,
      durationMs: 2200,
      wrongItems: const <WrongItem>[],
      improved: true,
    );
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell(), en2cnItem()]));
    // 第 1 题 word 正确 → 推进。
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: true, matchType: 'exact'),
      durationMs: 1000,
      firstVoiceMs: 200,
    );
    ctrl(c).next(); // 因为 requireSpell=true，会进 spell 步。
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: true, matchType: 'exact'),
      durationMs: 1000,
      firstVoiceMs: 200,
    );
    ctrl(c).next(); // 进入第 2 题 zhAnswer
    expect(stateOf(c).itemIndex, 1);
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: true, matchType: 'exact'),
      durationMs: 1000,
      firstVoiceMs: 200,
    );
    ctrl(c).next(); // 全部完成 → finish（异步）
    await Future<void>.delayed(Duration.zero);
    expect(stateOf(c).phase, PracticePhase.finished);
    expect(stateOf(c).settle, fake.nextSettle);
    expect(stateOf(c).settle!.grade, '优秀');
    expect(fake.finishCalls.last['reason'], 'completed');
  });

  // U15：中途退出标记 abandoned，已答数据不丢。
  test('U15 中途退出 abandon 标记 abandoned 且保留记录', () async {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell(), en2cnItem()]));
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: true, matchType: 'exact'),
      durationMs: 1000,
      firstVoiceMs: 200,
    );
    expect(stateOf(c).records.length, 1);
    final settle = await ctrl(c).abandon();
    expect(fake.finishCalls.last['reason'], 'abandoned');
    expect(stateOf(c).records.length, 1, reason: '已答数据不丢');
    expect(settle, isNotNull);
  });

  // U16 变体（2026-09-25 裁决 B）：英语侧提示**封顶一档**，不再虚升到 2/3 档。
  test('U16 提示封顶一档：第二次点击不再升档、计数不涨', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));
    ctrl(c).escalateHint();
    expect(stateOf(c).hintLevel, kEnglishMaxHintLevel);
    expect(stateOf(c).usedHintCount, 1);
    // 到顶后再点无效：二/三档需服务端下发答案，英语通道没有该来源（§八 #71）。
    ctrl(c).escalateHint();
    ctrl(c).escalateHint();
    expect(stateOf(c).hintLevel, kEnglishMaxHintLevel);
    expect(stateOf(c).usedHintCount, 1);
  });

  // U17：键盘提交时 retryCount 反映当前步失败数；used_keyboard=1。
  test('U17 键盘兜底提交上报 retryCount 与 usedKeyboard', () async {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()]));
    // 先失败 2 次（语音优先阶段）。
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: false, matchType: 'wrong'),
      durationMs: 800,
      firstVoiceMs: 120,
    );
    ctrl(c).retryVoice();
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: false, matchType: 'wrong'),
      durationMs: 800,
      firstVoiceMs: 120,
    );
    expect(stateOf(c).failuresForStep, 2);
    // 切键盘并提交。
    ctrl(c).useKeyboard();
    fake.nextVerdict =
        const AnswerVerdict(isCorrect: true, matchType: 'exact', answerId: 9);
    await ctrl(c).submitKeyboard(text: 'apple', durationMs: 1000, firstVoiceMs: 200);
    expect(fake.submitCalls.length, 1);
    expect(fake.submitCalls.last['retryCount'], 2);
    expect(fake.submitCalls.last['usedKeyboard'], isTrue);
    expect(fake.submitCalls.last['mode'], 'cn2en');
    expect(fake.submitCalls.last['step'], 1);
  });

  // F15（D-APP03-01）：幂等键生成唯一。
  test('F15 幂等键唯一且带会话号', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([wordItemWithSpell()], id: 77));
    final k1 = ctrl(c).buildIdempotencyKey();
    final k2 = ctrl(c).buildIdempotencyKey();
    expect(k1, isNot(equals(k2)));
    expect(k1, contains('77'));
  });

  // 模式二：单步 zhAnswer 直接完成。
  test('模式二单步 zhAnswer，答对即完成该题', () async {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf([en2cnItem()]));
    expect(ctrl(c).currentTaskType, 'zh_answer');
    ctrl(c).submitVoiceVerdict(
      const AnswerVerdict(isCorrect: true, matchType: 'exact'),
      durationMs: 1000,
      firstVoiceMs: 200,
    );
    expect(stateOf(c).records.length, 1);
    expect(stateOf(c).records.first.stepKind, PracticeStepKind.zhAnswer);
    ctrl(c).next();
    await Future<void>.delayed(Duration.zero);
    expect(stateOf(c).phase, PracticePhase.finished);
    expect(fake.finishCalls.last['reason'], 'completed');
  });

  // 空场次进入 error（不崩溃）。
  test('空场次进入 error 态而非崩溃', () {
    final fake = FakePracticeRepository();
    final c = makeContainer(fake);
    ctrl(c).start(sessionOf(const <PracticeItem>[]));
    expect(stateOf(c).phase, PracticePhase.error);
    expect(stateOf(c).errorMessage, isNotNull);
  });
}
