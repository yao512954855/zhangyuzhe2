/// WP-APP-03 **真后端联调** —— 打真实 uvicorn，验证英语练习的答题/结算/申诉契约。
///
/// 为什么非要这一层：`test/english_controller_test.dart` 喂的是**假仓库**，
/// 只能证明"我按自己的想象解析响应"。下面这些才是假数据测不出来的：
/// - `POST /api/sessions/{id}/answers` 的 §9.2 请求体（`seq/word_id/mode/step/
///   asr_text/used_keyboard/...`）后端到底认不认、返回有没有 `is_correct/match_type/diff`；
/// - 故意拼错时后端返回的 `diff` 字段齐不齐、能不能驱动 P7 逐字母回显；
/// - `POST /api/sessions/{id}/finish` 的 §9.3 结算字段（`total_items/correct_items/
///   accuracy/score/wrong_items/duration_ms`）齐不齐；
/// - `/api/answers/{id}/appeal` 到底接不接受（§8.9）；
/// - 组卷响应 §9.1 **不下发答案侧字段**（否则客户端等于拿到作弊入口）。
///
/// 后端没起时**自动跳过**（不算失败）—— 否则 `flutter test` 全量跑会莫名其妙红。
///
/// ⚠️ **必须 `HttpOverrides.global = null`**：`TestWidgetsFlutterBinding` 默认会装一个
/// 全局 `HttpOverrides`，把**任何**真实 HTTP 请求拦成 `400 + 空响应体`。
/// 不解开的话，这一层会「看起来全绿」—— 实际全是 skip，一个真请求都没发出去过。
///
/// 由 `scripts/smoke_app03.py`（先起后端 + 造数）自动执行。
library;

import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/net/api_client.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/features/auth/auth_controller.dart';
import 'package:student_app/features/config/practice_session.dart';
import 'package:student_app/features/config/practice_strategy.dart';
import 'package:student_app/features/english/practice_controller.dart';
import 'package:student_app/features/home/home_repository.dart';
import 'package:student_app/features/home/models.dart';

/// 真后端地址（与 `scripts/smoke_app03.py` 一致；脚本用 --dart-define 覆盖）。
const _host = String.fromEnvironment('APP03_SMOKE_HOST', defaultValue: '127.0.0.1');
const _port = int.fromEnvironment('APP03_SMOKE_PORT', defaultValue: 8891);

/// 造数用的年级学期 —— 必须与 `smoke_app03.py` 的 `SEED_GRADE/SEMESTER` 一致。
const _seedGrade = 3;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  // 解开 flutter_test 的 HTTP 拦截，否则所有真请求都会被吞成 400。
  HttpOverrides.global = null;

  var backendUp = false;
  late LocalStore localStore;

  setUpAll(() async {
    SharedPreferences.setMockInitialValues({});
    localStore = await LocalStore.open();
    final store = ServerStore(localStore);
    final api = ApiClient(serverStore: store, localStore: localStore);
    backendUp = await api.ping(
          base: const ServerAddress(host: _host, port: _port),
          timeout: const Duration(seconds: 3),
        ) !=
        null;
    if (!backendUp) {
      // ignore: avoid_print
      print('⚠️  后端 $_host:$_port 未启动，真机联调用例已跳过。');
    }
  });

  String uniqueUsername() =>
      'S${DateTime.now().microsecondsSinceEpoch % 1000000000}';

  /// 一个已注册且选了 3 年级上学期的学生（生产路径，token 真落在 LocalStore）。
  Future<(LocalStore, ApiClient, HomeRepository)> bootStudent() async {
    // 复用 setUpAll 的共享 LocalStore 实例（其内部 SharedPreferences 是单例）。
    // 不能在每次 bootStudent 里再 open() + setMockInitialValues：那样会新建一个与
    // setUpAll 实例脱钩的 mock，导致后续用该实例 override 的容器读不到 token，
    // 表现为「控制器路径 401 登录已过期、而直连 api 正常」。
    final local = localStore;
    final container = ProviderContainer(
      overrides: [localStoreProvider.overrideWithValue(local)],
    );
    addTearDown(container.dispose);
    await container
        .read(serverStoreProvider)
        .save(const ServerAddress(host: _host, port: _port));
    final api = container.read(apiClientProvider);

    await container.read(authControllerProvider.notifier).register(
          username: uniqueUsername(),
          realName: '联调小同学',
          password: '123456',
        );
    await api.put<Map<String, dynamic>>(
      '/api/me/profile',
      body: {'preferred_grade': _seedGrade, 'preferred_semester': 'first'},
    );
    return (local, api, HomeRepository(api: api, store: local));
  }

  /// 真实 English 单元 + 真实组卷响应（带答案侧字段校验）。
  Future<(HomeRepository, ApiClient, PracticeSession, Map<String, dynamic>)>
      bootSession() async {
    final (local, api, repo) = await bootStudent();
    final page = await repo.fetchUnits(subject: Subject.english);
    final unit = page.value.items.firstWhere((u) => u.subject == Subject.english);
    final data = await api.post<Map<String, dynamic>>(
      '/api/sessions',
      body: PracticeConfig(unit: unit).toRequest(),
    );
    final session = PracticeSession.fromJson(data!);
    return (repo, api, session, data);
  }

  // =========================================================== 真题提交（§9.2）
  group('真后端 · 答题 /api/sessions/{id}/answers（F1 / F7 / U7 / U17）', () {
    test('键盘提交 §9.2 请求体被后端接受，返回 is_correct/match_type', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, session, _) = await bootSession();
      final apple = session.items.firstWhere((i) => i.prompt == '苹果');
      final verdict = await HttpPracticeRepository(api: api).submitAnswer(
        sessionId: session.sessionId,
        seq: apple.seq,
        wordId: apple.wordId,
        mode: 'cn2en',
        step: 1,
        asrText: 'apple',
        durationMs: 1000,
        firstVoiceMs: 200,
        usedKeyboard: true,
        retryCount: 0,
        hintLevel: 0,
        usedHintCount: 0,
      );
      expect(verdict.isCorrect, isTrue, reason: 'apple 应判对');
      expect(verdict.matchType, isNotEmpty, reason: '§9.2 必须返回 match_type');
      expect(verdict.answerId, isNotNull, reason: '后端应下发 answer_id 供申诉');
    });

    test('拼读（step=2）故意拼错 → 返回 diff 且字段齐全（驱动 P7 逐字母回显）',
        () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, session, _) = await bootSession();
      final apple = session.items.firstWhere((i) => i.prompt == '苹果');
      // 拼读走 step=2：后端用 `judge_spell` 比字母序列并回溯出 diff（§6.2 / §5.4）。
      // 整词 step=1 的判分是 `judge_text`（相似度，1 字符差可能判 fuzzy 通过），
      // 不产生 diff，所以这里必须用 step=2 才能验到 P7 逐字母回显的数据。
      final verdict = await HttpPracticeRepository(api: api).submitAnswer(
        sessionId: session.sessionId,
        seq: apple.seq,
        wordId: apple.wordId,
        mode: 'cn2en',
        step: 2,
        asrText: 'aple',
        durationMs: 1000,
        firstVoiceMs: 200,
        usedKeyboard: true,
        retryCount: 1,
        hintLevel: 0,
        usedHintCount: 0,
      );
      expect(verdict.isCorrect, isFalse, reason: 'aple≠apple 应判错');
      expect(verdict.diff, isNotEmpty, reason: '拼错必须返回 diff 才能回显');
      final cell = verdict.diff.first;
      expect(cell.pos, isA<int>());
      expect(cell.expected, isA<String>());
      expect(cell.actual, isA<String>());
      expect(
        const {'match', 'wrong', 'missing', 'extra'},
        contains(cell.state),
        reason: '§10.4 diff.state 只可能是这四种',
      );
    });
  });

  // =========================================================== 结算（§9.3）
  group('真后端 · 结算 /api/sessions/{id}/finish（F16 / U14 / U15）', () {
    test('finish 返回结算字段齐全（total/correct/accuracy/score/wrong_items）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, session, _) = await bootSession();
      final settle = await HttpPracticeRepository(api: api).finish(
        sessionId: session.sessionId,
        reason: 'completed',
      );
      expect(settle.totalItems, session.items.length,
          reason: 'total_items 应等于组卷题数');
      expect(settle.correctItems, isA<int>());
      expect(settle.accuracy, isA<double>(), reason: 'accuracy 必须是数字');
      expect(settle.score, isA<double>(), reason: 'score 必须是数字');
      expect(settle.durationMs, isA<int>(), reason: '用时必须是数字');
      expect(settle.wrongItems, isA<List>());
      expect(settle.grade, isA<String>(), reason: '评级（优秀/良好/加油/继续练习）');
      for (final w in settle.wrongItems) {
        expect(w.wordId, isA<int>());
        expect(w.en, isA<String>());
        expect(w.cn, isA<String>());
        expect(w.yourAnswer, isA<String>());
      }
    });

    test('控制器整场 abandon → settle 与后端一致（U15 标记 abandoned）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, session, _) = await bootSession();
      final container = ProviderContainer(
        overrides: [localStoreProvider.overrideWithValue(localStore)],
      );
      addTearDown(container.dispose);
      container.listen(practiceControllerProvider, (_, _) {});
      await container
          .read(serverStoreProvider)
          .save(const ServerAddress(host: _host, port: _port));
      final ctrl = container.read(practiceControllerProvider.notifier);
      ctrl.start(session);
      // 先交一道（键盘兜底），再翻到下一题，再中途退出。
      await ctrl.submitKeyboard(text: 'apple', durationMs: 1000, firstVoiceMs: 200);
      ctrl.next();
      final settle = await ctrl.abandon();
      expect(settle, isNotNull);
      final settled = settle!;
      expect(settled.totalItems, session.items.length);
      expect(settled.grade, isA<String>());
      expect(container.read(practiceControllerProvider).phase,
          PracticePhase.finished);
    });
  });

  // =========================================================== 申诉（§8.9）
  group('真后端 · 申诉 /api/answers/{id}/appeal（F11 / U9）', () {
    test('appeal 端点可达（能申就申，不能申则中文业务错误）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, session, _) = await bootSession();
      final apple = session.items.firstWhere((i) => i.prompt == '苹果');
      final verdict = await HttpPracticeRepository(api: api).submitAnswer(
        sessionId: session.sessionId,
        seq: apple.seq,
        wordId: apple.wordId,
        mode: 'cn2en',
        step: 1,
        asrText: 'aple',
        durationMs: 1000,
        firstVoiceMs: 200,
        usedKeyboard: true,
        retryCount: 1,
        hintLevel: 0,
        usedHintCount: 0,
      );
      final answerId = verdict.answerId;
      expect(answerId, isNotNull, reason: '没有 answer_id 就无法申诉');
      try {
        await HttpPracticeRepository(api: api).appeal(
          answerId: answerId!,
          reason: '我读对了',
        );
      } on ApiException catch (e) {
        // 不能申（match_type 不是 fuzzy/loose）时后端应明确拒绝，且给中文。
        expect(RegExp(r'[\u4e00-\u9fa5]').hasMatch(e.displayText), isTrue,
            reason: '业务拒绝必须是中文提示，不能是英文异常');
      }
    });
  });

  // =========================================================== 组卷（§9.1）
  group('真后端 · 组卷响应不含答案侧字段（F1 / 共通约束）', () {
    test('组卷 items 不下发答案（en/cn/accepted/expected）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, session, raw) = await bootSession();
      final rawItems = (raw['items'] as List)
          .cast<Map<Object?, Object?>>()
          .cast<Map<String, dynamic>>();
      for (final item in rawItems) {
        expect(
          item.keys.toSet().intersection({'en', 'cn', 'accepted', 'expected'}),
          isEmpty,
          reason: '组卷响应不许带答案侧字段：$item',
        );
        // cn2en 模式题面是中文。
        expect(
          RegExp(r'[\u4e00-\u9fa5]').hasMatch(item['prompt']?.toString() ?? ''),
          isTrue,
          reason: 'cn2en 题面应是中文，实际：${item['prompt']}',
        );
      }
    });
  });
}
