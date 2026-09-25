/// WP-APP-04 **真后端联调** —— 打真实 uvicorn + 真实 WebSocket，验证语文背诵的服务端契约。
///
/// 为什么非要这一层：`test/test_app04_recite_test.dart` 喂的是**假仓库 / 假 WS**，
/// 只能证明"我按自己的想象解析响应"。下面这些才是假数据永远测不出来的：
/// - `POST /api/recitations` 的 F1 形状（`char_count_per_paragraph`、**不含正文/拼音**）；
/// - `POST /api/recitations/{id}/feed` 的 `newly_matched` 是**位置数组**而不是数字
///   —— 客户端的 `_toInt()` 对数组恒得 0，组件测试全绿也发现不了（真机上已修，
///   见本文件「位置数组」那条用例，它就是这个缺陷的**回归护栏**）；
/// - 「背诵范围不含首段」时字符切片不错位（BE-05 修掉的真实缺陷，靠 `咏鹅` 回归）；
/// - 提示三档的真实梯度（档 1 空框 / 档 2 下一个字 / 档 3 下一个字 + 后续 4 字）；
/// - `settle` **服务端重算**：客户端谎报 `matched_chars=9999` 必须无效（F5 防刷分，安全线）；
/// - WS `/ws/asr` 的 `task_type=recite` 回路：partial 帧真的推进服务端 `pos`。
///
/// 后端没起时**自动跳过**（不算失败）—— 否则 `flutter test` 全量跑会莫名其妙红。
///
/// ⚠️ **必须 `HttpOverrides.global = null`**：`TestWidgetsFlutterBinding` 默认会装一个
/// 全局 `HttpOverrides`，把**任何**真实 HTTP 请求拦成 `400 + 空响应体`。
/// 不解开的话，这一层会「看起来全绿」—— 实际全是 skip，一个真请求都没发出去过。
///
/// 由 `scripts/smoke_app04.py`（先起后端 + 造语文课文 + 把 ASR 切成 mock）自动执行。
library;

import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/net/api_client.dart';
import 'package:student_app/core/net/ws_client.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/features/auth/auth_controller.dart';
import 'package:student_app/features/config/practice_session.dart';
import 'package:student_app/features/config/practice_strategy.dart';
import 'package:student_app/features/home/home_repository.dart';
import 'package:student_app/features/home/models.dart';
import 'package:student_app/features/recite/recite_controller.dart';

/// 真后端地址（与 `scripts/smoke_app04.py` 一致；脚本用 --dart-define 覆盖）。
const _host = String.fromEnvironment('APP04_SMOKE_HOST', defaultValue: '127.0.0.1');
const _port = int.fromEnvironment('APP04_SMOKE_PORT', defaultValue: 8892);

/// 造数用的年级学期 —— 必须与 `smoke_app04.py` 的 `SEED_GRADE/SEMESTER` 一致。
const _seedGrade = 3;
const _seedSemester = 1;

/// 造数课文（`smoke_app04.py::_texts_xlsx`）。
const _title = '静夜思';
const _fullText = '床前明月光疑是地上霜举头望明月低头思故乡';
/// `smoke_app04.py` 里 mock ASR 的 `by_task.recite.final_text`。
const _mockText = '床前明月光';

/// 语文场次里「范围不含首段」的回归课文（背诵范围 = 第 2、3 段）。
const _scopedTitle = '咏鹅';

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

  /// 一个已注册、已选 3 年级上学期、且拿到**真语文场次**的上下文。
  ///
  /// 每个用例都新注册一个学生：服务端的背诵引擎按 `recitation_id` 缓存，
  /// 用同一学生会互相污染（settle 之后引擎就被丢掉了）。
  Future<_Fixture> bootFixture({String title = _title}) async {
    final container = ProviderContainer(
      overrides: [localStoreProvider.overrideWithValue(localStore)],
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
    final repo = HomeRepository(api: api, store: localStore);
    expect(
      await repo.syncPreference(grade: _seedGrade, semester: _seedSemester),
      isTrue,
      reason: '年级/学期偏好必须真的写到服务端，否则组卷查不到单元',
    );
    await localStore.setGradeSemester(grade: _seedGrade, semester: _seedSemester);

    final page = await repo.fetchUnits(
      subject: Subject.chinese,
      grade: _seedGrade,
      semester: _seedSemester,
    );
    final units =
        page.value.items.where((u) => u.subject == Subject.chinese).toList();
    expect(units, isNotEmpty,
        reason: '语文单元列表为空 —— 造数（语文课文导入）没生效？');
    final unit = units.first;

    final data = await api.post<Map<String, dynamic>>(
      '/api/sessions',
      body: PracticeConfig(unit: unit).toRequest(),
    );
    final session = PracticeSession.fromJson(data!);
    expect(session.contentType, 'chinese', reason: '语文场次的 content_type 必须是 chinese');
    final item = session.items.firstWhere(
      (e) => e.title == title,
      orElse: () => throw StateError(
        '场次里没有课文《$title》，实际有：${session.items.map((e) => e.title).toList()}',
      ),
    );
    return _Fixture(
      container: container,
      api: api,
      repo: ReciteRepository(api: api),
      session: session,
      textId: item.textId!,
      scope: item.reciteScope,
    );
  }

  // =========================================================== 连通性
  group('真后端 · 连通性', () {
    test('真实 HTTP 可达（ping 返回服务名与版本，证明请求真发出去了）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final store = ServerStore(localStore);
      final api = ApiClient(serverStore: store, localStore: localStore);
      final info = await api.ping(
        base: const ServerAddress(host: _host, port: _port),
        timeout: const Duration(seconds: 3),
      );
      expect(info, isNotNull, reason: 'ping 失败 = 请求没真发出去（或被 HttpOverrides 拦了）');
      expect(info!.service, 'bjsys');
      expect(info.version, isNotEmpty);
    });
  });

  // =========================================================== F1 创建背诵
  group('真后端 · 创建背诵 /api/recitations（F1 / U1 / U2）', () {
    test('create 返回契约字段：recitation_id / total_chars / 每段字数', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      expect(create.recitationId, greaterThan(0));
      expect(create.sessionId, f.session.sessionId);
      expect(create.textId, f.textId);
      expect(create.title, _title);
      expect(create.totalChars, _fullText.length,
          reason: '《静夜思》1 段全背 = ${_fullText.length} 字');
      expect(create.charCountPerParagraph, hasLength(f.scope.length),
          reason: '每段字数必须与背诵范围段数一致');
      expect(create.charCountPerParagraph.fold<int>(0, (a, b) => a + b),
          create.totalChars,
          reason: '每段字数之和必须等于 total_chars');
    });

    test('F1 响应不下发正文与拼音（安全线 F1）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final raw = await f.api.post<Map<String, dynamic>>(
        '/api/recitations',
        body: {
          'session_id': f.session.sessionId,
          'text_id': f.textId,
          'scope': f.scope,
        },
      );
      expect(raw, isNotNull);
      expect(
        raw!.keys.toSet().intersection(
          {'content_plain', 'content_pinyin', 'content_raw', 'paragraphs'},
        ),
        isEmpty,
        reason: 'F1 明文：响应不许带正文/拼音（否则等于把答案送到客户端）',
      );
      expect(raw['title'], _title, reason: '标题不算泄漏，应当下发');
    });

    test('scope=[] 取课文背诵范围；范围不含首段时切片不错位（BE-05 回归）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture(title: _scopedTitle);
      // 《咏鹅》3 段，背诵范围 = 第 2、3 段（"曲项向天歌" / "白毛浮绿水"，各 5 字）
      expect(f.scope, [1, 2], reason: '场次应下发导入时勾的背诵范围');
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: const <int>[],
      );
      expect(create.scope, [1, 2], reason: 'scope=[] 表示全文背诵 = 课文自己的背诵范围');
      expect(create.charCountPerParagraph, [5, 5],
          reason: '按全段累加会整体错位（少掉首段 3 字），这是 BE-05 修掉的缺陷');
      expect(create.totalChars, 10);
    });
  });

  // =========================================================== feed
  group('真后端 · 喂识别文本 /api/recitations/{id}/feed（F5 / U3）', () {
    test('feed 推进 matched_chars，且 newly_matched 是**位置数组**（真契约）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      final result = await f.repo.feed(create.recitationId, partialText: _mockText);

      expect(result.progress.pos, _mockText.length);
      expect(result.progress.matchedChars, _mockText.length,
          reason: 'matched_chars 必须真的推进（逐字点亮的唯一依据）');
      expect(result.progress.remaining, create.totalChars - _mockText.length);
      expect(result.progress.finished, isFalse);
      expect(result.isNoise, isFalse);

      // ⚠️ 契约护栏：服务端 `newly_matched` 是**位置数组**（BE-05 `FeedResult.newly_matched`）。
      //    早先客户端写成 `_toInt(json['newly_matched'])` → 对数组恒得 0，
      //    假适配器的组件测试永远绿。真机联调是唯一能发现它的地方。
      expect(result.newlyMatchedPositions, [0, 1, 2, 3, 4],
          reason: 'newly_matched 应为位置数组 [0..4]，而非计数');
      expect(result.newlyMatched, 5);
    });

    test('重复喂同一段累计文本不重复推进（累计语义）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      await f.repo.feed(create.recitationId, partialText: _mockText);
      final again = await f.repo.feed(create.recitationId, partialText: _mockText);
      expect(again.progress.pos, _mockText.length, reason: '累计文本不增 → 指针不动');
      expect(again.newlyMatched, 0);
      expect(again.newlyMatchedPositions, isEmpty);
      expect(again.isNoise, isFalse, reason: '重复文本不是噪声');
    });

    test('非中文内容判为噪声：不推进、不记错（需求 §6.3）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      final noise = await f.repo.feed(create.recitationId, partialText: 'hello world');
      expect(noise.isNoise, isTrue, reason: '抽不出汉字 → 服务端按噪声处理');
      expect(noise.progress.pos, 0, reason: '噪声不推进');
      expect(noise.progress.matchedChars, 0, reason: '噪声不记错也不点亮');
    });
  });

  // =========================================================== 提示
  group('真后端 · 提示 /api/recitations/{id}/hint（F7 / U6）', () {
    test('三档提示真实梯度：1=空框 / 2=下一个字 / 3=下一个字+后续 4 字', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      final id = create.recitationId;

      final lv1 = await f.repo.requestHint(id, level: 1, triggerBy: HintTrigger.manual);
      expect(lv1.level, 1);
      expect(lv1.content, isNotNull);
      expect(RegExp(r'^_+$').hasMatch(lv1.content!), isTrue,
          reason: '档 1 应是一排空框（下划线），实际：${lv1.content}');
      expect(lv1.content!.length, create.totalChars, reason: '空框数 = 剩余字数');

      final lv2 = await f.repo.requestHint(id, level: 2, triggerBy: HintTrigger.manual);
      expect(lv2.level, 2);
      expect(lv2.content, _fullText.substring(0, 1), reason: '档 2 给下一个字本身');

      final lv3 = await f.repo.requestHint(id, level: 3, triggerBy: HintTrigger.manual);
      expect(lv3.level, 3);
      expect(lv3.content, _fullText.substring(0, 5),
          reason: '档 3 = 下一个字 + 后续 4 字（中文单字粒度下与档 2 有真实梯度）');
      expect(lv3.content!.length, greaterThan(lv2.content!.length),
          reason: '三档必须有放水梯度，否则学生点了三档拿到跟二档一样的东西');
    });

    test('hint_count 按档累计（服务端扣分口径）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      final id = create.recitationId;
      await f.repo.requestHint(id, level: 1, triggerBy: HintTrigger.manual);
      await f.repo.requestHint(id, level: 1, triggerBy: HintTrigger.manual);
      final lv3 = await f.repo.requestHint(id, level: 3, triggerBy: HintTrigger.manual);
      expect(lv3.hintCount[1], 2);
      expect(lv3.hintCount[3], 1);
      // 服务端 `hint_count` **三档键恒存在**（引擎初始化为 `{"1":0,"2":0,"3":0}`），
      // 没用过的档位是 0 而不是缺键 —— 所以这里断 0，不是断 null。
      // （客户端 `hintTotal` 用 `?? 0` 累加，两种形状都安全。）
      expect(lv3.hintCount[2], 0, reason: '没要过档 2 → 计数为 0（键仍在）');
      expect(lv3.hintCount.keys.toSet(), {1, 2, 3});
    });
  });

  // =========================================================== 重开 / 补报
  group('真后端 · 重开与断线补报（F9 / F15）', () {
    test('restart 把指针与已点亮记录归零，restart_count 递增', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      await f.repo.feed(create.recitationId, partialText: _mockText);
      final after = await f.repo.restart(create.recitationId);
      expect(after.pos, 0);
      expect(after.matchedChars, 0);
      expect(after.restartCount, 1, reason: '重开次数必须服务端记（结算会用它扣分）');
    });

    test('events 批量补报被接受，accepted 计数如实返回（F2）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      final accepted = await f.repo.reportEvents(create.recitationId, [
        ReciteEvent.restart(pos: 0),
        ReciteEvent.hint(pos: 3, hintLevel: 2, triggerBy: HintTrigger.manual),
      ]);
      expect(accepted, 2);
      // 空列表直接短路返回 0（不发请求）
      expect(await f.repo.reportEvents(create.recitationId, const []), 0);
    });
  });

  // =========================================================== 结算
  group('真后端 · 结算 /api/recitations/{id}/settle（F12 / F13 / F5）', () {
    test('settle 六项齐、评级是中文，且二次提交走幂等回放', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      await f.repo.feed(create.recitationId, partialText: _fullText);
      final settle = await f.repo.settle(
        create.recitationId,
        transcript: _fullText,
        restartCount: 0,
        durationMs: 4200,
        firstVoiceMs: 300,
        finishReason: FinishReason.completed,
      );
      expect(settle.recitationId, create.recitationId);
      expect(settle.totalChars, create.totalChars, reason: '总字数');
      expect(settle.matchedChars, create.totalChars, reason: '已背字数');
      expect(settle.missedCount, 0, reason: '全背出来了 → 漏字 0');
      expect(settle.missedDetail, isEmpty);
      expect(settle.coveredRatio, closeTo(1.0, 1e-9), reason: '覆盖率');
      expect(settle.score, greaterThan(0), reason: '评分');
      expect(
        RegExp(r'[\u4e00-\u9fa5]').hasMatch(settle.grade),
        isTrue,
        reason: '评级应是中文（优秀/良好/加油/继续练习），实际：${settle.grade}',
      );
      expect(settle.hintCount, isNotNull);
      expect(settle.replay, isFalse, reason: '首次结算是真算，不是回放');

      // 幂等：再来一次 → 直接回放原结果，不重复写库
      final again = await f.repo.settle(
        create.recitationId,
        transcript: _fullText,
        restartCount: 0,
        durationMs: 4200,
        finishReason: FinishReason.completed,
      );
      expect(again.replay, isTrue, reason: '已结算过的背诵再提交应走幂等回放');
      expect(again.matchedChars, settle.matchedChars);
      expect(again.score, settle.score);
    });

    test('服务端重算：客户端谎报 matched_chars=9999 必须无效（F5 防刷分）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      // 一个字都没喂，却在 body 里谎报全背出来了
      final raw = await f.api.post<Map<String, dynamic>>(
        '/api/recitations/${create.recitationId}/settle',
        body: {
          'transcript': '',
          'matched_chars': 9999,
          'missed_chars': <int>[],
          'hint_count': <String, int>{'1': 0},
          'restart_count': 0,
          'duration_ms': 1000,
          'finish_reason': 'manual',
        },
      );
      expect(raw, isNotNull);
      expect(raw!['matched_chars'], 0,
          reason: 'F5 安全线：客户端上报的 matched_chars 只收不用，服务端必须重算');
      expect(raw['total_chars'], create.totalChars);
      expect(raw['missed_count'], create.totalChars, reason: '一个字没背 → 全是漏字');
      expect(raw['covered_ratio'], 0.0);
    });
  });

  // =========================================================== 控制器状态机
  group('真后端 · 控制器走真实状态机（F1→F16）', () {
    test('start → feed → hint → restart → settle 全程打通', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final container = f.container;
      container.listen(reciteControllerProvider, (_, _) {});
      final ctrl = container.read(reciteControllerProvider.notifier);

      final create = await ctrl.start(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );
      expect(container.read(reciteControllerProvider).phase, RecitePhase.ready);
      expect(create.totalChars, greaterThan(0));

      await ctrl.feed(_mockText);
      expect(container.read(reciteControllerProvider).phase, RecitePhase.reciting);
      expect(container.read(reciteControllerProvider).progress!.pos,
          _mockText.length);

      expect(
        await ctrl.requestHint(level: 1, triggerBy: HintTrigger.manual),
        isTrue,
        reason: '手动要提示必须拿得到',
      );
      expect(container.read(reciteControllerProvider).hint!.level, 1);

      await ctrl.restart();
      expect(container.read(reciteControllerProvider).progress!.restartCount, 1);
      expect(container.read(reciteControllerProvider).progress!.pos, 0);

      // 重开之后从头背一遍，再结算
      await ctrl.feed(_fullText);
      final settle = await ctrl.settle(
        transcript: _fullText,
        durationMs: 3000,
        firstVoiceMs: 200,
        finishReason: FinishReason.completed,
      );
      expect(container.read(reciteControllerProvider).phase, RecitePhase.settled);
      expect(settle.matchedChars, create.totalChars);
      // 重开次数由服务端记账（控制器把它带进 settle）
      expect(settle.hintCount[1], 1, reason: '之前手动要过一次档 1');
    });
  });

  // =========================================================== WS 语音链路
  group('真后端 · WS /ws/asr 的 task_type=recite（F5 流式通道）', () {
    test('partial 帧真的推进服务端 pos，end 后收到 final + verdict', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final f = await bootFixture();
      final create = await f.repo.create(
        sessionId: f.session.sessionId,
        textId: f.textId,
        scope: f.scope,
      );

      f.container.listen(wsClientProvider, (_, _) {});
      final ws = f.container.read(wsClientProvider);
      final frames = <Map<String, dynamic>>[];
      final sub = ws.messages.listen(frames.add);
      addTearDown(() async {
        await sub.cancel();
        await ws.dispose();
      });

      await ws.connect();
      expect(ws.state, WsState.connected,
          reason: 'WS 没连上：后端 /ws/asr 不可达，或 token 没落进 LocalStore');

      ws.sendStart(
        WsStartOptions(
          sessionId: f.session.sessionId,
          taskType: WsTaskType.recite,
          recitationId: create.recitationId,
        ),
      );
      final started = await _waitForFrame(frames, (m) => m['type'] == 'started');
      expect(started['provider'], 'mock',
          reason: '烟测必须走 mock ASR —— provider 不是 mock 说明 ASR_CONFIG_PATH 没生效');

      // 推 12 片 150ms 裸 PCM（16k/单声道/16bit）。Mock 每片产一个 partial。
      for (var i = 0; i < 12; i++) {
        expect(ws.sendAudio(_pcmChunk()), isTrue, reason: '第 $i 片音频没发出去');
        await Future<void>.delayed(const Duration(milliseconds: 60));
      }
      ws.sendEnd();

      final partial =
          await _waitForFrame(frames, (m) => m['type'] == 'partial' && m['recite'] != null);
      final progress = (partial['recite'] as Map).cast<String, dynamic>();
      expect(progress['pos'], greaterThan(0),
          reason: 'WS partial 必须真的推进服务端背诵指针（否则"逐字点亮"没有服务端口径）');
      expect(progress['total_chars'], create.totalChars);

      final finalFrame = await _waitForFrame(frames, (m) => m['type'] == 'final');
      expect(finalFrame['text'], _mockText);

      final verdict = await _waitForFrame(
        frames,
        (m) => m['type'] == 'verdict' && m['task_type'] == 'recite',
      );
      final recite = (verdict['recite'] as Map).cast<String, dynamic>();
      expect(recite['total_chars'], create.totalChars);
      expect(recite['matched_chars'], greaterThan(0));

      // 心跳也在同一条连接上验一下（契约 §6.2）
      ws.sendPing();
      final pong = await _waitForFrame(frames, (m) => m['type'] == 'pong');
      expect(pong['type'], 'pong');
    });
  });
}

// ---------------------------------------------------------------------------
// 测试夹具
// ---------------------------------------------------------------------------

class _Fixture {
  _Fixture({
    required this.container,
    required this.api,
    required this.repo,
    required this.session,
    required this.textId,
    required this.scope,
  });

  final ProviderContainer container;
  final ApiClient api;
  final ReciteRepository repo;
  final PracticeSession session;
  final int textId;
  final List<int> scope;
}

/// 150ms / 16kHz / 单声道 / 16bit 裸 PCM（服务端要求偶数长度且 ≤ 32000 字节）。
Uint8List _pcmChunk() => Uint8List(16000 * 2 * 150 ~/ 1000);

/// 在 [frames] 里等一帧（轮询，最长 [timeout]）——WS 是推送式，测试要等它到。
Future<Map<String, dynamic>> _waitForFrame(
  List<Map<String, dynamic>> frames,
  bool Function(Map<String, dynamic>) match, {
  Duration timeout = const Duration(seconds: 10),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(deadline)) {
    for (final frame in frames) {
      if (match(frame)) return frame;
    }
    await Future<void>.delayed(const Duration(milliseconds: 50));
  }
  throw StateError(
    '等 WS 帧超时（${timeout.inSeconds}s）。已收到的帧类型：'
    '${frames.map((f) => f['type']).toList()}',
  );
}
