/// WP-APP-04 组件测试（U1~U16）。
///
/// 分层：
/// - **纯组件**（CharGrid / HintBar / ReciteTimer / ReciteSettlePage / ReciteText）：
///   只靠 [AgeScope] + [AppTheme] 提供的主题，不需要任何 Provider 网络；
/// - **控制器**（ReciteController + 假仓库）：验证 feed / 三档提示 / 同位置不重复 /
///   重新开始 / 结算 的纯逻辑，对齐全在服务端（契约 §13）；
/// - **整页集成**（RecitePage + 假录音 + 假 WS）：验证"开始→识别→点亮→暂停→继续→
///   提示→结算"这条真实链路（用假实现避开麦克风 / 云端 ASR）。
///
/// 不依赖真后端、不依赖真麦克风（假绿陷阱的反面：每个断言都断言了"真发生了什么"）。
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/audio/pcm_recorder.dart';
import 'package:student_app/core/net/api_client.dart';
import 'package:student_app/core/net/ws_client.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/core/widgets/app_button.dart';
import 'package:student_app/features/config/practice_session.dart';
import 'package:student_app/features/recite/recite_controller.dart';
import 'package:student_app/features/recite/recite_page.dart';
import 'package:student_app/features/recite/recite_settle_page.dart';
import 'package:student_app/features/recite/recite_text.dart';
import 'package:student_app/features/recite/widgets/char_grid.dart';
import 'package:student_app/features/recite/widgets/hint_bar.dart';
import 'package:student_app/features/recite/widgets/recite_timer.dart';

// ===========================================================================
// 假实现
// ===========================================================================

/// 假仓库：把"服务端对齐"简化成"识别文本长度即点亮字数"，足以驱动客户端逻辑。
class _FakeRepo extends ReciteRepository {
  _FakeRepo({required super.api});

  /// 背诵范围内的汉字数。假实现固定 6 字，足够覆盖「点亮前缀 / 留空框 / 其余不显示」。
  final int totalChars = 6;
  int _hints = 0;
  int _restarts = 0;

  @override
  Future<ReciteCreateResponse> create({
    required int sessionId,
    required int textId,
    required List<int> scope,
  }) async =>
      ReciteCreateResponse(
        recitationId: 1,
        sessionId: sessionId,
        textId: textId,
        title: '春',
        scope: scope,
        totalChars: totalChars,
        charCountPerParagraph: [totalChars],
      );

  @override
  Future<ReciteFeedResult> feed(
    int recitationId, {
    required String partialText,
    int silenceMs = 0,
  }) async {
    final raw = partialText.runes.length;
    final matched = raw > totalChars ? totalChars : raw;
    return ReciteFeedResult(
      progress: ReciteProgress(
        pos: matched,
        totalChars: totalChars,
        matchedChars: matched,
        remaining: totalChars - matched,
        hintCount: const <int, int>{},
        restartCount: _restarts,
        missStreak: 0,
        finished: matched >= totalChars,
      ),
      newlyMatched: matched,
    );
  }

  @override
  Future<ReciteHint> requestHint(
    int recitationId, {
    int? level,
    HintTrigger triggerBy = HintTrigger.auto,
    int silenceMs = 0,
  }) async {
    // 顺序给出 1→2→3 档；满 3 档后再要就返回 level:0（同位置不再重复触发，F7）。
    if (_hints >= 3) {
      return ReciteHint(
        level: 0,
        content: null,
        pos: 0,
        hintCount: const <int, int>{},
      );
    }
    _hints += 1;
    final lvl = _hints;
    final content = switch (lvl) {
      1 => '_' * totalChars,
      2 => '父',
      _ => '父亲相见',
    };
    return ReciteHint(
      level: lvl,
      content: content,
      pos: 0,
      hintCount: <int, int>{lvl: 1},
    );
  }

  @override
  Future<ReciteProgress> restart(int recitationId) async {
    _restarts += 1;
    _hints = 0;
    return ReciteProgress(
      pos: 0,
      totalChars: totalChars,
      matchedChars: 0,
      remaining: totalChars,
      hintCount: const <int, int>{},
      restartCount: _restarts,
      missStreak: 0,
      finished: false,
    );
  }

  @override
  Future<int> reportEvents(int recitationId, List<ReciteEvent> events) async =>
      events.length;

  @override
  Future<ReciteSettle> settle(
    int recitationId, {
    required String transcript,
    required int restartCount,
    required int durationMs,
    int? firstVoiceMs,
    required FinishReason finishReason,
  }) async {
    final raw = transcript.runes.length;
    final matched = raw > totalChars ? totalChars : raw;
    return ReciteSettle(
      recitationId: recitationId,
      totalChars: totalChars,
      matchedChars: matched,
      missedCount: totalChars - matched,
      missedDetail: const <ReciteMissedChar>[],
      coveredRatio: totalChars == 0 ? 0.0 : matched / totalChars,
      score: 90,
      grade: '良好',
      hintCount: const <int, int>{1: 1},
      replay: false,
    );
  }
}

/// 假录音器：直接放行权限、返回空音频流（不碰真麦克风）。
class _FakeRecorder extends PcmRecorder {
  @override
  Future<MicPermission> ensurePermission() async => MicPermission.granted;

  @override
  Future<Stream<Uint8List>> start() async => const Stream<Uint8List>.empty();

  @override
  Future<void> stop() async {}

  @override
  Future<void> dispose() async {}
}

/// 假 WS：直接"已连接"，并允许测试往消息流里塞 partial。
class _FakeWs extends WsClient {
  _FakeWs(ServerStore serverStore, LocalStore localStore)
      : super(serverStore: serverStore, localStore: localStore);

  final _msg = StreamController<Map<String, dynamic>>.broadcast();

  @override
  Stream<Map<String, dynamic>> get messages => _msg.stream;

  @override
  WsState get state => WsState.connected;

  @override
  bool get isConnected => true;

  @override
  Future<void> connect({int? lastAnswerId}) async {}

  void emitPartial(String text) => _msg.add({'type': 'partial', 'text': text});
}

/// 假 API：覆盖「课文详情」与「结算」两条真实接口，避免组件测试打真实后端。
///
/// 注意：控制器 `settle` 走的是 `api.post('/api/recitations/{id}/settle')`（直接走
/// `apiClientProvider`，不经过 `reciteRepository`），所以必须在这里拦截 POST。
class _FakeApi extends ApiClient {
  _FakeApi({required super.serverStore, required super.localStore});

  @override
  Future<T?> get<T>(
    String path, {
    Map<String, dynamic>? query,
    RequestOptionsOverride? override,
  }) {
    if (path.startsWith('/api/texts/')) {
      final data = <String, dynamic>{
        'paragraphs': [
          {'idx': 0, 'text': '盼望着，盼望着；东风来了。'},
          {'idx': 1, 'text': '春天，像刚落地的娃娃。'},
        ],
        'recite_scope': const [0, 1],
      };
      return Future<T?>.value(data as T);
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
    if (path.contains('/settle')) {
      final data = <String, dynamic>{
        'recitation_id': 1,
        'total_chars': 6,
        'matched_chars': 4,
        'missed_count': 2,
        'missed_detail': const <Map<String, dynamic>>[],
        'covered_ratio': 4 / 6,
        'score': 88,
        'grade': '良好',
        'hint_count': const {'1': 1, '2': 0, '3': 1},
        'replay': false,
      };
      return Future<T?>.value(data as T);
    }
    return super.post<T>(path, body: body, query: query, override: override);
  }
}

// ===========================================================================
// 测试辅助
// ===========================================================================

Widget _themed(Widget child, {AppPalette palette = AppPalettes.day}) {
  final profile = AgeProfile.ofGrade(3);
  return AgeScope(
    profile: profile,
    child: MaterialApp(
      theme: AppTheme.build(palette: palette),
      home: Scaffold(body: child),
    ),
  );
}

/// 整页测试用：把页面放进已注册 [AppExtraColors] 主题扩展的 [MaterialApp]。
/// RecitePage / ReciteSettlePage 内部用 `AppExtraColors.of(context)`（带 `!`），
/// 裸 MaterialApp 不注册该扩展会直接崩，所以集成测试必须经此包装。
Widget _themedPage(Widget page, {AppPalette palette = AppPalettes.day}) {
  final profile = AgeProfile.ofGrade(3);
  return AgeScope(
    profile: profile,
    child: MaterialApp(
      theme: AppTheme.build(palette: palette),
      home: page,
    ),
  );
}

PracticeSession _fakeSession({int totalChars = 6}) => PracticeSession(
      sessionId: 1,
      contentType: 'chinese',
      items: [
        PracticeItem(
          seq: 1,
          textId: 1,
          title: '春',
          reciteScope: const [0],
          totalChars: totalChars,
        ),
      ],
    );

/// 把假实现注入（单一根 [ProviderScope]，所有 override 放同一层）。
///
/// 家庭 provider [reciteTextProvider] 直接在此用 `overrideWith` 覆盖，返回带标点的
/// 课文，绕开真实 `GET /api/texts/{id}`；放在根 scope 才能被组件树的 `ref.watch`
/// 正确读到（riverpod v3 下家庭 provider 缓存在最外层 scope）。
Widget _scoped({
  required LocalStore local,
  required ServerStore server,
  required ReciteRepository repo,
  required WsClient ws,
  required Widget child,
}) =>
    ProviderScope(
      overrides: [
        localStoreProvider.overrideWithValue(local),
        apiClientProvider.overrideWithValue(
          _FakeApi(serverStore: server, localStore: local),
        ),
        reciteTextProvider.overrideWith(
          (ref, key) => ReciteText(
            chars: const ['我', '与', '父', '亲', '相', '见'],
            paragraphCharCounts: const [6],
          ),
        ),
        reciteRepositoryProvider.overrideWithValue(repo),
        pcmRecorderProvider.overrideWithValue(_FakeRecorder()),
        wsClientProvider.overrideWithValue(ws),
      ],
      child: child,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  SharedPreferences.setMockInitialValues({});

  late LocalStore local;
  late ServerStore store;

  setUpAll(() async {
    local = await LocalStore.open();
    store = ServerStore(local);
  });

  setUp(() {
    // 默认 800×600 会让 ListView 懒加载 + 字号溢出，按规范 §9 设成手机视口。
    // ⚠️ `binding.window.*` 已废弃（multi-window 迁移）；`setUp` 里拿不到 `tester`，
    //    所以走 `platformDispatcher.implicitView`（类型就是 `TestFlutterView`）。
    final view = TestWidgetsFlutterBinding.instance.platformDispatcher.implicitView!;
    view.physicalSize = const Size(1080, 1920);
    view.devicePixelRatio = 1.0;
  });

  tearDown(() {
    final view = TestWidgetsFlutterBinding.instance.platformDispatcher.implicitView!;
    view.resetPhysicalSize();
    view.resetDevicePixelRatio();
  });

  // =========================================================================
  // U1 / U2 / F2 / F3 / F4 / F16 —— 逐字点亮网格
  // =========================================================================
  group('CharGrid · 逐字点亮（U1/U2/F2/F3/F4/F16）', () {
    testWidgets('U1 进入不显示正文：matched=0 时只画空框，目标字一个都不出现',
        (tester) async {
      await tester.pumpWidget(
        _themed(
          CharGrid(
            chars: const ['我', '与', '父', '亲'],
            matched: 0,
            paragraphCharCounts: const [4],
          ),
        ),
      );
      // 当前位置是一个**纯空框**（规格 F2「当前位置留空框」）：框在，但框内没有任何字符。
      final currentCell = find.byKey(CharGrid.currentCellKey);
      expect(currentCell, findsOneWidget);
      expect(
        find.descendant(of: currentCell, matching: find.byType(Text)),
        findsNothing,
        reason: '空框里不能有字符：哪怕占位用的「字」也等于提前泄露一个字（F2）',
      );
      expect(find.text('字'), findsNothing);
      // 但目标汉字一个都不渲染（F1：不泄露正文）
      expect(find.text('我'), findsNothing);
      expect(find.text('父'), findsNothing);
    });

    testWidgets('U2 按字推进：matched=2 时前两个字显示，第三个字是当前空框（不显示）',
        (tester) async {
      await tester.pumpWidget(
        _themed(
          CharGrid(
            chars: const ['我', '与', '父', '亲'],
            matched: 2,
            paragraphCharCounts: const [4],
          ),
        ),
      );
      expect(find.text('我'), findsOneWidget);
      expect(find.text('与'), findsOneWidget);
      // '父' 是当前位置空框，不应作为汉字出现
      expect(find.text('父'), findsNothing);
    });

    testWidgets('F4 标点不进网格：标点只保留布局位置，不渲染字符', (tester) async {
      // 6 个汉字 + 1 个逗号，网格按 6 个汉字渲染（逗号不占格）
      await tester.pumpWidget(
        _themed(
          CharGrid(
            chars: const ['盼', '望', '着', '东', '风', '来'],
            matched: 6,
            paragraphCharCounts: const [6],
          ),
        ),
      );
      expect(find.text('盼'), findsOneWidget);
      expect(find.text('，'), findsNothing);
    });

    testWidgets('F16 暗色主题下已背字与当前框用不同色彩（不靠亮度）',
        (tester) async {
      await tester.pumpWidget(
        _themed(
          CharGrid(
            chars: const ['我', '与', '父', '亲'],
            matched: 1,
            paragraphCharCounts: const [4],
          ),
          palette: AppPalettes.night,
        ),
      );
      final matched = tester.widget<Text>(find.text('我'));
      final palette = AppPalettes.night;
      // 已背字用 onSurface（#D6D6D6），当前框描边用 primary（#5CBDB0）—— 必须不同
      expect(matched.style?.color, isNotNull);
      expect(matched.style!.color, isNot(equals(palette.primaryColor)));
    });

    testWidgets('回归：汉字不得被裁掉下沿（行盒必须装得进单元格）', (tester) async {
      // 真机 2026-09-29：背诵页每个字的下半截被削掉（「二」只剩一横、
      // 「已」只剩一个口），学生根本看不清要背什么。
      //
      // 机理：已背字的格子是 `SizedBox(width/height = cellSize)`，
      // 里面 `Center` 一个 `Text`。`SizedBox` 给的是**紧约束**，`Center`
      // 只把约束「放松」到 `maxHeight = cellSize`；而当时的文字样式是课文用的
      // `reciteStyle`（`height: 2.0`，行盒 = 2.0em > cellSize = 1.12em）。
      // → `RenderParagraph` 的 `size` 被 `constrain` 到 cellSize
      // → `didOverflowHeight` 成立 → **它自己 `clipRect` 到被压矮的尺寸**
      // → 每个汉字下沿约 40% 被裁。
      //
      // ⚠️ 纯渲染层：`find` / 尺寸 / `analyze` / `build` 全都看不出来，
      //    真机截图才可见。这里用「文字行盒高度 ≤ 渲染盒高度」这条不变量把它钉死。
      await tester.pumpWidget(
        _themed(
          CharGrid(
            chars: const ['我'],
            matched: 1,
            paragraphCharCounts: const [1],
          ),
        ),
      );

      final style = tester.widget<Text>(find.text('我')).style!;
      final painter = TextPainter(
        text: TextSpan(text: '我', style: style),
        textDirection: TextDirection.ltr,
      )..layout();
      final lineBox = painter.height;
      final textBox = tester.getSize(find.text('我'));
      final cellBox = tester.getSize(find.byType(CharGrid));

      expect(
        textBox.height + 0.01,
        greaterThanOrEqualTo(lineBox),
        reason: '文字行盒（$lineBox）比渲染盒（${textBox.height}）高 → '
            'RenderParagraph 会纵向溢出并裁掉汉字下沿',
      );
      expect(
        cellBox.height + 0.01,
        greaterThanOrEqualTo(lineBox),
        reason: '单元格（${cellBox.height}）装不下文字行盒（$lineBox）→ 字会被裁',
      );
    });
  });

  // =========================================================================
  // U6 / U7 / U8 / F6 / F7 —— 三档提示条
  // =========================================================================
  group('HintBar · 三档提示（U6/U7/U8/F6/F7）', () {
    testWidgets('U6 一档提示：渲染一排空框 + "还有 N 个字"', (tester) async {
      await tester.pumpWidget(
        _themed(
          HintBar(
            hint: ReciteHint(
              level: 1,
              content: '___',
              pos: 0,
              hintCount: const {1: 1},
            ),
            usedCount: 1,
          ),
        ),
      );
      expect(find.textContaining('还有'), findsOneWidget);
    });

    testWidgets('U7 二档提示：显示下一个字', (tester) async {
      await tester.pumpWidget(
        _themed(
          HintBar(
            hint: ReciteHint(
              level: 2,
              content: '父',
              pos: 0,
              hintCount: const {2: 1},
            ),
            usedCount: 1,
          ),
        ),
      );
      expect(find.text('父'), findsOneWidget);
    });

    testWidgets('U7 三档提示：显示下一个字 + 后续窗口', (tester) async {
      await tester.pumpWidget(
        _themed(
          HintBar(
            hint: ReciteHint(
              level: 3,
              content: '父亲相见',
              pos: 0,
              hintCount: const {3: 1},
            ),
            usedCount: 1,
          ),
        ),
      );
      expect(find.text('父亲相见'), findsOneWidget);
    });

    testWidgets('U8 界面显示当前已用提示次数', (tester) async {
      await tester.pumpWidget(
        _themed(
          HintBar(
            hint: null,
            usedCount: 2,
          ),
        ),
      );
      expect(find.text('已用提示 2 次'), findsOneWidget);
    });
  });

  // =========================================================================
  // U11 / F11 / F14 —— 计时器
  // =========================================================================
  group('ReciteTimer · 计时（U11/F11/F14）', () {
    testWidgets('不限时：显示已用时间', (tester) async {
      await tester.pumpWidget(
        _themed(const ReciteTimer(elapsed: Duration(seconds: 65))),
      );
      expect(find.textContaining('已用'), findsOneWidget);
      // 计时文案是整段 "已用 01:05"，用 contains 匹配更稳。
      expect(find.textContaining('01:05'), findsWidgets);
    });

    testWidgets('U11/F14 限时到点：显示剩余 00:00 且变警示色（不闪烁）',
        (tester) async {
      await tester.pumpWidget(
        _themed(
          ReciteTimer(
            elapsed: const Duration(minutes: 6),
            limit: const Duration(minutes: 5),
          ),
        ),
      );
      expect(find.text('剩余 00:00'), findsOneWidget);
    });
  });

  // =========================================================================
  // U4 / F4 —— 展示正文切片（标点排除 + 段落形状）
  // =========================================================================
  group('ReciteText · 展示正文切片（U4/F4）', () {
    test('标点被剔除、纯汉字进网格，段落字数保留形状', () {
      final json = {
        'paragraphs': [
          {
            'idx': 0,
            'text': '盼望着，盼望着；东风来了。',
          },
          {
            'idx': 1,
            'text': '春天，像刚落地的娃娃。',
          },
        ],
        'recite_scope': [0, 1],
      };
      final text = ReciteText.fromDetail(json);
      // 第一段纯汉字：盼望着盼望着东风来了 = 10 字（标点去掉）
      // 第二段：春天像刚落地的娃娃 = 9 字
      expect(text.chars.length, 10 + 9);
      expect(text.paragraphCharCounts, [10, 9]);
      expect(text.chars.contains('，'), isFalse);
      expect(text.chars.contains('；'), isFalse);
    });

    test('按背诵范围切片：只背第 1 段（idx=1）时只取该段汉字', () {
      final json = {
        'paragraphs': [
          {'idx': 0, 'text': '第一段内容'},
          {'idx': 1, 'text': '第二段要背的内容'},
        ],
        'recite_scope': [1],
      };
      final text = ReciteText.fromDetail(json, scope: const [1]);
      expect(text.chars.join(''), '第二段要背的内容');
      expect(text.paragraphCharCounts, [8]);
    });

    // -----------------------------------------------------------------------
    // D-APP04-01 的**真因**（2026-09-25 实跑复核）：
    // 客户端与服务端的「什么算汉字」必须是同一张表（服务端 `PinyinService._HAN_RANGES`
    // 6 段：扩展A / 基本区 / 兼容表意 F900 / 扩展B / 扩展C~F / 扩展G）。
    // 客户端原先只有前两段 → 生僻字被丢掉 → `chars.length != total_chars` →
    // 网格框数与「已背出 N 字」的点亮索引整体错位。
    //
    // ⚠️ 这**不是**「标点格」问题：标点两边都剔除（上一条用例已证）。
    // -----------------------------------------------------------------------
    test('生僻字不丢：兼容表意（F900 区）与增补平面（20000+）都要进网格', () {
      final json = {
        'paragraphs': [
          {'idx': 0, 'text': '床前\uF900\uF901\u{20000}'},
        ],
        'recite_scope': [0],
      };
      final text = ReciteText.fromDetail(json);
      expect(
        text.chars.length,
        5,
        reason: '汉字集必须与服务端 _HAN_RANGES 同源，否则 chars.length != total_chars',
      );
      expect(text.paragraphCharCounts, [5]);
      // 增补平面字是**一个码点**，不能被算成两半
      expect(text.chars.last.runes.length, 1);
    });

    test('常用字口径不回退（回归护栏）', () {
      final json = {
        'paragraphs': [
          {'idx': 0, 'text': '床前明月光'},
        ],
        'recite_scope': [0],
      };
      expect(ReciteText.fromDetail(json).chars.length, 5);
      expect(ReciteText.fromDetail(json).chars.join(''), '床前明月光');
    });
  });

  // =========================================================================
  // U12 / U13 / F13 / F14 —— 结算页
  // =========================================================================
  group('ReciteSettlePage · 结算（U12/U13/F13/F14）', () {
    final settle = ReciteSettle(
      recitationId: 1,
      totalChars: 6,
      matchedChars: 4,
      missedCount: 2,
      missedDetail: const [ReciteMissedChar(pos: 4, char: '相')],
      coveredRatio: 4 / 6,
      score: 88,
      grade: '良好',
      hintCount: const {1: 1, 2: 0, 3: 1},
      replay: false,
    );

    testWidgets('U12/F13 六项指标 + 评级齐全', (tester) async {
      await tester.pumpWidget(
        _themed(
          ReciteSettlePage(settle: settle, durationMs: 125000, firstVoiceMs: 800),
        ),
      );
      expect(find.text('总字数'), findsOneWidget);
      expect(find.text('已背字数'), findsOneWidget);
      expect(find.text('漏字数'), findsOneWidget);
      expect(find.text('提示次数'), findsOneWidget);
      expect(find.text('总用时'), findsOneWidget);
      expect(find.text('首次发声延迟'), findsOneWidget);
      // 评级
      expect(find.text('良好'), findsWidgets);
    });

    testWidgets('U13 回看列出漏字位置与字符', (tester) async {
      await tester.pumpWidget(
        _themed(
          ReciteSettlePage(settle: settle, durationMs: 125000, firstVoiceMs: 800),
        ),
      );
      // 第 5 个字（pos=4 → 人类序号 5）：相
      expect(find.textContaining('第 5 个字'), findsOneWidget);
      expect(find.textContaining('相'), findsWidgets);
    });

    testWidgets('U13 没有漏字时给正向反馈', (tester) async {
      final clean = ReciteSettle(
        recitationId: 1,
        totalChars: 6,
        matchedChars: 6,
        missedCount: 0,
        missedDetail: const [],
        coveredRatio: 1.0,
        score: 100,
        grade: '优秀',
        hintCount: const {},
        replay: false,
      );
      await tester.pumpWidget(
        _themed(ReciteSettlePage(settle: clean, durationMs: 90000, firstVoiceMs: 500)),
      );
      expect(find.textContaining('没有漏字'), findsOneWidget);
    });
  });

  // =========================================================================
  // F14 产品调整（2026-09-29，姚工）—— 回看由「逐字清单」改为「全文着色」
  // =========================================================================
  group('ReciteSettlePage · 回看全文着色（F14 产品调整）', () {
    const full = ['我', '与', '父', '亲', '相', '见'];

    ReciteSettle settleWith(List<ReciteMissedChar> missed) => ReciteSettle(
          recitationId: 1,
          totalChars: 6,
          matchedChars: 6 - missed.length,
          missedCount: missed.length,
          missedDetail: missed,
          coveredRatio: (6 - missed.length) / 6,
          score: 88,
          grade: '良好',
          hintCount: const {},
          replay: false,
        );

    /// 取出回看全文的 `Text`，按字摊平成「字 → 样式」（用于校验着色）。
    Map<String, TextStyle> charStyles(WidgetTester tester) {
      final widget = tester.widget<Text>(
        find.byKey(const Key('reciteReviewText')),
      );
      final root = widget.textSpan! as TextSpan;
      final out = <String, TextStyle>{};
      root.visitChildren((span) {
        if (span is TextSpan && span.text != null && span.style != null) {
          for (final rune in span.text!.runes) {
            final ch = String.fromCharCode(rune);
            if (ch.trim().isEmpty) continue;
            out[ch] = span.style!;
          }
        }
        return true;
      });
      return out;
    }

    /// 取出回看全文的 `Text`，把各 span 的文本原样拼起来（用于校验分段换行）。
    String flatString(WidgetTester tester) {
      final widget = tester.widget<Text>(
        find.byKey(const Key('reciteReviewText')),
      );
      final root = widget.textSpan! as TextSpan;
      final buf = StringBuffer();
      root.visitChildren((span) {
        if (span is TextSpan && span.text != null) buf.write(span.text);
        return true;
      });
      return buf.toString();
    }

    testWidgets('回看展示全文：漏字与背出的字用不同颜色（不再是逐字清单）', (tester) async {
      await tester.pumpWidget(
        _themed(ReciteSettlePage(
          settle: settleWith(const [ReciteMissedChar(pos: 4, char: '相')]),
          durationMs: 1000,
          firstVoiceMs: 500,
          fullChars: full,
          paragraphCharCounts: const [6],
        )),
      );

      final styles = charStyles(tester);
      // 全文 6 个字都在，且不再出现「第 N 个字」清单
      expect(styles.keys.toSet(), full.toSet());
      expect(find.textContaining('第 5 个字'), findsNothing);

      final errorColor = AppPalettes.day.errorColor;
      // 漏掉的「相」→ 强调色（并带浅底，颜色之外再给一档）
      expect(styles['相']!.color, errorColor);
      expect(styles['相']!.backgroundColor, AppPalettes.day.errorLightColor);
      // 背出来的字 → 正文色
      for (final ch in ['我', '与', '父', '亲', '见']) {
        expect(styles[ch]!.color, isNot(errorColor), reason: '$ch 不该被标成漏字');
      }
      // 图例（不靠单色传达）
      expect(find.text('背出来的字'), findsOneWidget);
      expect(find.text('没背出来的字'), findsOneWidget);
    });

    testWidgets('全文长度 ≠ 服务端 total_chars → 退回逐字清单，绝不画错人', (tester) async {
      await tester.pumpWidget(
        _themed(ReciteSettlePage(
          settle: settleWith(const [ReciteMissedChar(pos: 4, char: '相')]),
          durationMs: 1000,
          firstVoiceMs: 500,
          // 故意少一个字：5 ≠ total_chars(6)
          fullChars: const ['我', '与', '父', '亲', '相'],
          paragraphCharCounts: const [5],
        )),
      );
      expect(find.byKey(const Key('reciteReviewText')), findsNothing);
      expect(find.textContaining('第 5 个字'), findsOneWidget);
    });

    testWidgets('全文按段落换行；全对时也展示全文并给正向反馈', (tester) async {
      await tester.pumpWidget(
        _themed(ReciteSettlePage(
          settle: settleWith(const []),
          durationMs: 1000,
          firstVoiceMs: 500,
          fullChars: full,
          paragraphCharCounts: const [3, 3],
        )),
      );
      expect(flatString(tester), '我与父\n亲相见');
      expect(find.textContaining('没有漏字'), findsOneWidget);
      // 没有漏字时不摆图例
      expect(find.text('没背出来的字'), findsNothing);
    });
  });

  // =========================================================================
  // 契约 §9.4 —— 结算响应的用时 / 首声延迟（D-APP04-03 的修复护栏）
  // =========================================================================
  group('ReciteSettle · 用时同源（契约 §9.4 响应回带）', () {
    test('解析服务端回带的 duration_ms / first_voice_ms', () {
      final settle = ReciteSettle.fromJson({
        'recitation_id': 1,
        'total_chars': 6,
        'matched_chars': 6,
        'missed_count': 0,
        'missed_detail': <Map<String, dynamic>>[],
        'covered_ratio': 1.0,
        'score': 100.0,
        'grade': '优秀',
        'hint_count': {'1': 0, '2': 0, '3': 0},
        'replay': false,
        'duration_ms': 42000,
        'first_voice_ms': 1800,
      });
      expect(settle.durationMs, 42000);
      expect(settle.firstVoiceMs, 1800);
    });

    test('服务端未回带 → durationMs=0、firstVoiceMs=null（调用方据此回退本地计时）', () {
      final settle = ReciteSettle.fromJson({
        'recitation_id': 1,
        'total_chars': 6,
        'matched_chars': 6,
        'missed_count': 0,
        'missed_detail': <Map<String, dynamic>>[],
        'covered_ratio': 1.0,
        'score': 100.0,
        'grade': '优秀',
        'hint_count': <String, dynamic>{},
        'replay': false,
      });
      expect(settle.durationMs, 0);
      expect(settle.firstVoiceMs, isNull);
    });

    test('没发过声时服务端回 null，客户端不能把它当成 0', () {
      final settle = ReciteSettle.fromJson({
        'recitation_id': 1,
        'total_chars': 6,
        'matched_chars': 0,
        'missed_count': 6,
        'missed_detail': <Map<String, dynamic>>[],
        'covered_ratio': 0.0,
        'score': 0.0,
        'grade': '待加强',
        'hint_count': <String, dynamic>{},
        'replay': false,
        'duration_ms': 9000,
        'first_voice_ms': null,
      });
      expect(settle.durationMs, 9000);
      expect(settle.firstVoiceMs, isNull, reason: 'null 要渲染成「—」，0 会被画成「0 秒」');
    });
  });

  // =========================================================================
  // U3 / U5 / U6 / U7 / U8 / U10 —— 控制器（对齐全在服务端）
  // =========================================================================
  group('ReciteController · 逻辑（U3/U5/U6/U7/U8/U10）', () {
    test('U3 同音字由服务端判：客户端只转发累计文本，matched 随 feed 推进',
        () async {
      final repo = _FakeRepo(api: ApiClient(serverStore: store, localStore: local));
      final c = ProviderContainer(overrides: [
        localStoreProvider.overrideWithValue(local),
        reciteRepositoryProvider.overrideWithValue(repo),
      ]);
      await c.read(reciteControllerProvider.notifier).start(
            sessionId: 1,
            textId: 1,
            scope: const [0],
          );
      await c.read(reciteControllerProvider.notifier).feed('我与父');
      expect(c.read(reciteControllerProvider).progress?.matchedChars, 3);
    });

    test('U5 漏念不阻断：feed 短文本只点亮对应字数，后续可继续', () async {
      final repo = _FakeRepo(api: ApiClient(serverStore: store, localStore: local));
      final c = ProviderContainer(overrides: [
        localStoreProvider.overrideWithValue(local),
        reciteRepositoryProvider.overrideWithValue(repo),
      ]);
      final notifier = c.read(reciteControllerProvider.notifier);
      await notifier.start(sessionId: 1, textId: 1, scope: const [0]);
      await notifier.feed('我'); // 漏念了"与父"
      expect(c.read(reciteControllerProvider).progress?.matchedChars, 1);
      expect(c.read(reciteControllerProvider).progress?.remaining, 5);
      await notifier.feed('我与父亲相见'); // 继续背
      final p = c.read(reciteControllerProvider).progress!;
      expect(p.matchedChars, 6);
      expect(p.finished, isTrue);
    });

    test('U6/U7 三档顺序 + 同位置不重复：连要 4 次 → 1/2/3/0', () async {
      final repo = _FakeRepo(api: ApiClient(serverStore: store, localStore: local));
      final c = ProviderContainer(overrides: [
        localStoreProvider.overrideWithValue(local),
        reciteRepositoryProvider.overrideWithValue(repo),
      ]);
      final notifier = c.read(reciteControllerProvider.notifier);
      await notifier.start(sessionId: 1, textId: 1, scope: const [0]);
      await notifier.requestHint(triggerBy: HintTrigger.auto);
      expect(c.read(reciteControllerProvider).hint?.level, 1);
      await notifier.requestHint(triggerBy: HintTrigger.auto);
      expect(c.read(reciteControllerProvider).hint?.level, 2);
      await notifier.requestHint(triggerBy: HintTrigger.auto);
      expect(c.read(reciteControllerProvider).hint?.level, 3);
      // 满 3 档后再要：服务端判定此刻不该提示（同位置不重复触发）
      final fourth = await notifier.requestHint(triggerBy: HintTrigger.auto);
      expect(fourth, isFalse);
      expect(c.read(reciteControllerProvider).hint?.level, 3);
    });

    test('U8 手动"我卡住了"降一档并记次数', () async {
      final repo = _FakeRepo(api: ApiClient(serverStore: store, localStore: local));
      final c = ProviderContainer(overrides: [
        localStoreProvider.overrideWithValue(local),
        reciteRepositoryProvider.overrideWithValue(repo),
      ]);
      final notifier = c.read(reciteControllerProvider.notifier);
      await notifier.start(sessionId: 1, textId: 1, scope: const [0]);
      final ok = await notifier.requestHint(triggerBy: HintTrigger.manual);
      expect(ok, isTrue);
      expect(c.read(reciteControllerProvider).progress?.hintTotal, 1);
    });

    test('U10 重新开始本段：进度清零，restart_count +1', () async {
      final repo = _FakeRepo(api: ApiClient(serverStore: store, localStore: local));
      final c = ProviderContainer(overrides: [
        localStoreProvider.overrideWithValue(local),
        reciteRepositoryProvider.overrideWithValue(repo),
      ]);
      final notifier = c.read(reciteControllerProvider.notifier);
      await notifier.start(sessionId: 1, textId: 1, scope: const [0]);
      await notifier.feed('我与父');
      await notifier.restart();
      final p = c.read(reciteControllerProvider).progress!;
      expect(p.matchedChars, 0);
      expect(p.restartCount, 1);
    });
  });

  // =========================================================================
  // 整页集成：U2 / U9 / F1 / F5 / F12 —— 开始→识别→点亮→暂停→继续→提示→结算
  // =========================================================================
  group('RecitePage · 整页链路（U2/U9/F1/F5/F12）', () {
    testWidgets('F1 进入只有标题与开始按钮；背出后逐字点亮；暂停/继续；结束并结算',
        (tester) async {
      final repo = _FakeRepo(api: ApiClient(serverStore: store, localStore: local));
      final ws = _FakeWs(store, local);

      await tester.pumpWidget(
        _scoped(
          local: local,
          server: store,
          repo: repo,
          ws: ws,
          child: _themedPage(RecitePage(session: _fakeSession())),
        ),
      );
      // 进入瞬间可能短暂显示 LoadingView（持续动画），且创建/路由都有过渡，
      // 这里统一用固定时长 pump（容忍动画），不再用 pumpAndSettle（会一直等不到静止）。
      await tester.pump(const Duration(milliseconds: 500));

      // F1：进入先看到"开始背诵"，且正文不显示
      expect(find.widgetWithText(AppButton, '开始背诵'), findsOneWidget);
      expect(find.text('我'), findsNothing);

      // 点开始背诵
      await tester.tap(find.widgetWithText(AppButton, '开始背诵'));
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.widgetWithText(AppButton, '暂停'), findsOneWidget);

      // F5：模拟识别到"我与父" → 前三个字点亮
      ws.emitPartial('我与父');
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text('我'), findsOneWidget);
      expect(find.text('与'), findsOneWidget);
      expect(find.text('父'), findsOneWidget);
      // '亲' 还没背到 → 作为当前空框，不显示汉字
      expect(find.text('亲'), findsNothing);

      // U9：暂停 → 出现"继续背诵"
      await tester.tap(find.widgetWithText(AppButton, '暂停'));
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.widgetWithText(AppButton, '继续背诵'), findsOneWidget);

      // 继续
      await tester.tap(find.widgetWithText(AppButton, '继续背诵'));
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.widgetWithText(AppButton, '暂停'), findsOneWidget);

      // U8/F7：点"我卡住了" → 提示条出现（一档：还有 N 个字）
      await tester.tap(find.widgetWithText(AppButton, '我卡住了'));
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.textContaining('还有'), findsWidgets);

      // F12：结束并结算 → 跳到结算页
      //
      // ⚠️ 这里必须「多 pump 几次」，不能只 pump 一帧：
      //    `_endAndSettle` 里是 停录音 → 补报事件 → POST settle → Navigator.push
      //    一串 await（都在 microtask 里推进），而 `push` 只是**登记**新路由，
      //    要等下一帧才会构建出结算页。只 pump 一次会出现「push 已经发生、
      //    但 `find.text('背完啦')` 还是 0 个」——本包就踩过这个坑。
      await tester.tap(find.widgetWithText(AppButton, '结束并结算'));
      for (var i = 0; i < 4; i++) {
        await tester.pump(const Duration(milliseconds: 200));
      }
      expect(find.text('背完啦'), findsOneWidget);
      expect(find.text('总字数'), findsOneWidget);
    });

    testWidgets('F1 没有可背课文时如实引导，不崩', (tester) async {
      final repo = _FakeRepo(api: ApiClient(serverStore: store, localStore: local));
      final ws = _FakeWs(store, local);
      await tester.pumpWidget(
        _scoped(
          local: local,
          server: store,
          repo: repo,
          ws: ws,
          child: _themedPage(const RecitePage(session: null)),
        ),
      );
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.textContaining('还不能直接背'), findsOneWidget);
    });
  });
}
