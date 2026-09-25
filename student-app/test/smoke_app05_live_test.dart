/// WP-APP-05 **真后端联调** —— 打真实 uvicorn，验证错题本 / 我的记录·统计的数据契约。
///
/// 为什么非要这一层：`test/test_app05_*.dart` 喂的是**手写 JSON**，只能证明
/// "我按自己的想象解析响应"。下面这些才是假数据测不出来的（APP-05 专属）：
/// - `/api/me/wrong-words` 的字段到底叫 `word_id` / `wrong_count` / `en` / `cn`
///   （错题本页 `WrongWord` 模型直接读这些字段）；
/// - `scope=wrong` 组卷走**生产 `PracticeConfig.toRequest()`**，后端认不认这个字段名
///   （`StrategyIn` 是 `extra="ignore"`，拼错不报错、静默按默认出题，最阴的坑）；
/// - `/api/me/progress` 的字段齐全且都是数字（我的记录 / 统计页直接拼字符串）；
/// - `GET /api/sessions` / `GET /api/sessions/{id}`（契约 §14.1 缺口 7 → §9.5）：
///   列表字段、`started_at DESC` 排序、`ongoing` 场次的 `score` 为 `null`，
///   以及**防泄题线** —— 进行中的场次拒绝下发逐题明细，结算后才给 `expected`。
///
/// 由 `scripts/smoke_app05.py`（先起后端 + 造错题）自动执行。
///
/// ⚠️ **必须 `HttpOverrides.global = null`**：`TestWidgetsFlutterBinding` 默认会装一个
/// 全局 `HttpOverrides`，把**任何**真实 HTTP 请求拦成 `400 + 空响应体`。
/// 不解开的话，这一层会「看起来全绿」—— 实际全是 skip，一个真请求都没发出去过。
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
import 'package:student_app/features/config/practice_strategy.dart';
import 'package:student_app/features/home/home_repository.dart';
import 'package:student_app/features/home/models.dart';

/// 真后端地址（与 `scripts/smoke_app05.py` 一致；脚本用 --dart-define 覆盖）。
const _host = String.fromEnvironment('APP05_SMOKE_HOST', defaultValue: '127.0.0.1');
const _port = int.fromEnvironment('APP05_SMOKE_PORT', defaultValue: 8893);

/// 造数用的烟测学生账号（Python 造好、这里登录，二者通过 --dart-define 对齐）。
const _user = String.fromEnvironment('APP05_SMOKE_USER', defaultValue: 'smoke05');
const _pass = String.fromEnvironment('APP05_SMOKE_PASSWORD', defaultValue: 'smoke05pass');

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  // 解开 flutter_test 的 HTTP 拦截，否则所有真请求都会被吞成 400。
  HttpOverrides.global = null;

  var backendUp = false;

  /// ⚠️ **契约 §5.4：单账号登录 5 次/分钟**（`RateLimitMiddleware`，超限 `10005`）。
  ///
  /// 所以整套用例**只登录一次**。原先的写法是「每个用例各调一次 `bootStudent()`」，
  /// 5 条用例刚好卡在 5 次上限上；用例数一到 6，第 6 条就会收到
  /// `10005 操作太频繁了`。那**不是**限流的 bug，是限流在正常工作
  /// —— 但会让烟测变成一条随时会红的脆弱护栏，所以改为共享登录。
  late LocalStore sharedLocal;
  late ApiClient sharedApi;
  ProviderContainer? sharedContainer;

  setUpAll(() async {
    SharedPreferences.setMockInitialValues({});
    final local = await LocalStore.open();
    sharedLocal = local;

    final probe = ApiClient(serverStore: ServerStore(local), localStore: local);
    final pong = await probe.ping(
      base: ServerAddress(host: _host, port: _port),
      timeout: const Duration(seconds: 3),
    );
    backendUp = pong != null;
    if (!backendUp) {
      // ignore: avoid_print
      print('⚠️  后端 $_host:$_port 未启动，真机联调用例已跳过。');
      return;
    }

    /// 登录走生产路径（ProviderContainer + AuthController），
    /// 保证 token 真的落在 LocalStore 里，后续 `api.get` 自动带 Authorization。
    final container = ProviderContainer(
      overrides: [localStoreProvider.overrideWithValue(local)],
    );
    sharedContainer = container;
    await container
        .read(serverStoreProvider)
        .save(ServerAddress(host: _host, port: _port));
    sharedApi = container.read(apiClientProvider);
    await container
        .read(authControllerProvider.notifier)
        .login(username: _user, password: _pass);
  });

  tearDownAll(() async {
    sharedContainer?.dispose();
  });

  /// 复用 `setUpAll` 里那**唯一一次**登录（**别再登一次** —— 见上面的限流说明）。
  Future<(LocalStore, ApiClient)> bootStudent() async => (sharedLocal, sharedApi);

  Future<UnitItem> englishUnit(HomeRepository repo) async {
    final page = await repo.fetchUnits();
    return page.value.items.firstWhere((u) => u.subject == Subject.english);
  }

  // =========================================================== 探活 / 登录
  group('真后端 · 探活与登录（证明真请求真的发出去了）', () {
    test('HttpOverrides 已解开：/api/ping 返回契约字段 service=="bjsys"', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final local = await LocalStore.open();
      final api = ApiClient(
        serverStore: ServerStore(local),
        localStore: local,
      );
      final pong = await api.ping(
        base: ServerAddress(host: _host, port: _port),
        timeout: const Duration(seconds: 3),
      );
      expect(pong, isNotNull, reason: '真请求必须真的到达后端');
      expect(pong!.service, 'bjsys', reason: '契约字段，证明不是被 HttpOverrides 拦成的假响应');
    });

    test('登录烟测学生 → token 落地（后续请求自动带 Authorization）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      // 登录已在 `setUpAll` 里做过一次（限流：5 次/分钟，不再重复登）。
      expect(sharedLocal.token, isNotNull, reason: '登录态应落在 LocalStore');
      expect(sharedLocal.token!.isNotEmpty, isTrue);
    });
  });

  // =========================================================== 错题本契约
  group('真后端 · 错题本数据源（U1 / F1 红线：字段逐个核对）', () {
    test('GET /api/me/wrong-words 字段逐个核对（WrongWord 模型靠这些字段渲染）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api) = await bootStudent();
      final raw = await api.get<Map<String, dynamic>>('/api/me/wrong-words');
      final data = raw!;
      // ApiClient 已解包到 data：{items, total, page, page_size}
      final items = (data['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(items, isNotEmpty, reason: 'smoke_app05.py 已制造错题，不该为空');
      for (final it in items) {
        expect(it['word_id'], isA<int>(), reason: '§8.8 应有 word_id');
        expect(it['unit_id'], isA<int>(), reason: '§8.8 应有 unit_id');
        expect(it['wrong_count'], isA<int>(), reason: '§8.8 应有 wrong_count');
        expect(it['wrong_count'] as int > 0, isTrue, reason: '错题本只列 wrong_count>0 的词');
        expect(it['en'], isA<String>(), reason: '错题本要显示英文');
        expect(it['cn'], isA<String>(), reason: '错题本要显示中文');
      }
    });

    test('POST /api/sessions scope=wrong（走生产 toRequest）→ 题全部来自错题本', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (local, api) = await bootStudent();
      final repo = HomeRepository(api: api, store: local);
      final unit = await englishUnit(repo);

      // **走生产代码的请求体构造** —— 这条用例的价值一半在于 `toRequest()` 的字段名
      final config = PracticeConfig(unit: unit, scope: PracticeScope.wrong);
      final data = await api.post<Map<String, dynamic>>(
        '/api/sessions',
        body: config.toRequest(),
      );
      expect(data, isNotNull);
      final items = (data!['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(items, isNotEmpty, reason: '错题本非空，scope=wrong 应组出题');

      // 错题本里的 word_id 集合
      final ww = await api.get<Map<String, dynamic>>('/api/me/wrong-words');
      final wwItems = (ww!['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      final wwIds = wwItems.map((e) => e['word_id'] as int).toSet();

      for (final it in items) {
        expect(
          wwIds.contains(it['word_id']),
          isTrue,
          reason: 'scope=wrong 出的题必须来自错题本：${it['word_id']} 不在 $wwIds',
        );
      }
    });
  });

  // =========================================================== 进度契约
  group('真后端 · 进度总览（U3 / U4 / F2 / F3 的数据源）', () {
    test('GET /api/me/progress 字段齐全且都是数字（我的记录/统计页直接拼字符串）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api) = await bootStudent();
      final raw = await api.get<Map<String, dynamic>>('/api/me/progress');
      final p = raw!;
      for (final key in const [
        'total_words',
        'practiced_words',
        'mastered_words',
        'streak_days',
        'accuracy',
        'today',
      ]) {
        expect(p.containsKey(key), isTrue, reason: '§8.4 应有字段 $key');
      }
      expect(p['total_words'], isA<int>());
      expect(p['practiced_words'], isA<int>());
      expect(p['mastered_words'], isA<int>());
      expect(p['streak_days'], isA<int>());
      expect(p['accuracy'], isA<num>(), reason: '正确率口径是数字（0.0），不是 null');
      expect(p['today'], isA<Map>(), reason: '今日统计是对象');
    });
  });

  // =========================================================== 我的记录（§9.5）
  group('真后端 · 我的记录场次列表 / 单次详情（契约 §14.1 缺口 7 → §9.5）', () {
    /// 走生产组卷造一条**真实**场次，返回 `(session_id, 卷面 items)`。
    ///
    /// 这条用例的一半价值在于：`/api/sessions` **同一路径两种语义**
    /// （`POST` = 创建、`GET` = 列表）。假适配器曾因只按 path 匹配，
    /// 把创建响应喂给列表 —— 这里用真后端做对照面。
    Future<(int, List<Map<String, dynamic>>)> createRealSession(
      ApiClient api,
      HomeRepository repo,
    ) async {
      final unit = await englishUnit(repo);
      final created = await api.post<Map<String, dynamic>>(
        '/api/sessions',
        body: PracticeConfig(unit: unit, scope: PracticeScope.unit).toRequest(),
      );
      expect(created, isNotNull, reason: '组卷必须成功');
      final paper =
          (created!['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(paper, isNotEmpty, reason: '组卷应出题');
      return (created['session_id'] as int, paper);
    }

    test('GET /api/sessions：字段逐个核对 + 只回自己 + started_at 倒序', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (local, api) = await bootStudent();
      final repo = HomeRepository(api: api, store: local);
      final (sessionId, _) = await createRealSession(api, repo);

      final data = (await api.get<Map<String, dynamic>>(
        '/api/sessions',
        query: {'page': 1, 'page_size': 20},
      ))!;
      expect(data['page'], isA<int>());
      expect(data['page_size'], isA<int>());
      expect(data['total'], isA<int>());
      final items = (data['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(items, isNotEmpty, reason: '刚 POST 创建过场次，列表不该为空');

      for (final it in items) {
        expect(it['session_id'], isA<int>(), reason: '§9.5.1 应有 session_id');
        expect(it['content_type'], anyOf('english', 'chinese'));
        expect(it['status'], anyOf('ongoing', 'finished', 'abandoned'));
        expect(it['total_items'], isA<int>());
        expect(it['correct_items'], isA<int>());
        expect(it['accuracy'], isA<num>(), reason: 'accuracy 是数字，不是 null');
        expect(it['duration_ms'], isA<int>(), reason: 'ongoing 给 0，也不能是 null');
        expect(it['started_at'], isA<String>());
      }

      // §9.5.1 排序：`started_at DESC`
      final stamps = items.map((e) => e['started_at'] as String).toList();
      final expectedOrder = [...stamps]..sort((a, b) => b.compareTo(a));
      expect(stamps, expectedOrder, reason: '最新在前（started_at DESC）');

      // 刚创建的那条是 ongoing：**没有成绩，也不拿 0 分冒充**。
      final mine = items.firstWhere((e) => e['session_id'] == sessionId);
      expect(mine['status'], 'ongoing');
      expect(mine['score'], isNull, reason: 'ongoing 场次 score 必须是 null');
      expect(mine['ended_at'], isNull);
      expect(mine['duration_ms'], 0);
    });

    test('GET /api/sessions/{id}：ongoing 拒绝逐题明细（防泄题）→ 结算后才给 expected', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (local, api) = await bootStudent();
      final repo = HomeRepository(api: api, store: local);
      final (sessionId, paper) = await createRealSession(api, repo);

      // ① 进行中：服务端必须拒绝（10003），**绝不下发 `expected`** —— 那就是答案。
      await expectLater(
        api.get<Map<String, dynamic>>('/api/sessions/$sessionId'),
        throwsA(
          isA<ApiException>().having((e) => e.code, 'code', 10003),
        ),
        reason: '进行中场次的逐题明细含 expected，属泄题，必须拒绝',
      );

      // ② 答一题。**这一步不是为了验判分**（那是 APP-03 的事），只为造出
      //    「有作答记录的项」—— `items[]` 只列这类项，见下面第 ③ 步的说明。
      final first = paper.first;
      final verdict = await api.post<Map<String, dynamic>>(
        '/api/sessions/$sessionId/answers',
        body: {
          'word_id': first['word_id'],
          'mode': first['mode'],
          'step': 1,
          'seq': first['seq'],
          'asr_text': 'zzz-deliberately-wrong',
          'duration_ms': 1200,
          'used_keyboard': true,
        },
      );
      expect(verdict, isNotNull, reason: '§9.2 提交必须被接受（否则这条用例的前提就断了）');

      // ③ 结算之后才允许复盘。
      final finished = await api.post<Map<String, dynamic>>(
        '/api/sessions/$sessionId/finish',
        body: {'reason': 'manual'},
      );
      expect(finished, isNotNull, reason: '结算必须成功');

      final detail = (await api.get<Map<String, dynamic>>(
        '/api/sessions/$sessionId',
      ))!;
      expect(detail['session_id'], sessionId);
      expect(detail['status'], anyOf('finished', 'abandoned'));
      expect(detail['duration_ms'], isA<int>());
      expect(detail['ended_at'], isA<String>());
      // `total_items` 是**组卷时**落库的项数（题序不落库，见 §9.5.2 口径）。
      expect(
        detail['total_items'],
        paper.length,
        reason: '场次级 total_items 就是当时组卷的项数',
      );

      final items =
          (detail['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(items, isNotEmpty, reason: '答过 1 项，逐项明细至少 1 条');
      // ⚠️ `items.length` **可以小于** `total_items`：答案表里没有记录的项不列
      //    （题序没落库，服务端不还原「当时出了哪些题」—— 还原不出且会撒谎）。
      expect(
        items.length,
        lessThanOrEqualTo(detail['total_items'] as int),
        reason: 'items 只列有作答记录的项',
      );
      for (final it in items) {
        expect(it['state'], anyOf('correct', 'wrong', 'empty'));
        expect(it.containsKey('expected'), isTrue, reason: '§9.5.2 已结束场次要给 expected');
      }
    });
  });
}
