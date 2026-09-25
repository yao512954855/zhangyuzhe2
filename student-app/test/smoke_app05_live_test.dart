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
///   以及**防泄题线** —— 进行中的场次拒绝下发逐题明细，结算后才给 `expected`；
/// - `GET /api/me/wrong-chars`（**§8.10，A5 新增**）：字段是 `char` / `text_title` /
///   `first_pos` / `context_before|after`，**与 §8.8 的词条形状毫无交集** ——
///   两个端点的字段名**必须各自核一遍**，别以为"错题本嘛，差不多"。
///   尤其 `first_pos`：下标与正文错位是这类接口最常见的**静默**错误
///   （页面照样渲染，只是上下文取错位置），所以这里拿真课文正文**逐字核对**。
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

/// 造数用的语文课文（**必须与 `scripts/smoke_app05.py` 里的常量逐字一致**）。
/// Python 侧真背一遍并故意漏掉 `_missChar`，这里拿同一份正文核对上下文。
const _textBody = '床前明月光疑是地上霜举头望明月低头思故乡';
const _missChar = '霜';
const _missPos = 9;
/// Python 侧背了 2 遍 → `wrong_count` 必须是 2（验证次数真的在累加）。
const _reciteTimes = 2;

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

  // ======================================================= 语文错题本（§8.10）
  group('真后端 · 语文错题本（A5，§8.10 —— 独立端点，别与 §8.8 串）', () {
    test('GET /api/me/wrong-chars 字段逐个核对 + 上下文来自真课文正文', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api) = await bootStudent();
      final raw = await api.get<Map<String, dynamic>>('/api/me/wrong-chars');
      final data = raw!;
      final items = (data['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(items, isNotEmpty, reason: 'smoke_app05.py 已真背漏一个字，不该为空');

      for (final it in items) {
        expect(it['text_id'], isA<int>(), reason: '§8.10 应有 text_id');
        expect(it['text_title'], isA<String>(), reason: '§8.10 应有 text_title');
        expect(it['unit_id'], isA<int>(), reason: '§8.10 应有 unit_id');
        expect(it['unit_name'], isA<String>(), reason: '§8.10 应有 unit_name');
        expect(it['char'], isA<String>(), reason: '§8.10 应有 char');
        expect((it['char'] as String).length, 1, reason: '错题单位是「一个字」');
        expect(it['wrong_count'], isA<int>());
        expect(it['wrong_count'] as int > 0, isTrue, reason: '只列漏过的字');
        expect(it['first_pos'], isA<int>());
        expect(it['context_before'], isA<String>());
        expect(it['context_after'], isA<String>());
        // §8.8 的字段**不该**出现在这里（两个端点两个形状，串了就是 bug）
        expect(it.containsKey('word_id'), isFalse, reason: '§8.10 不是词条，别混字段');
        expect(it.containsKey('en'), isFalse, reason: '§8.10 不是词条，别混字段');
      }

      // ⚠️ 核心断言：`first_pos` 必须真的指向那个字，上下文必须真的来自正文。
      //    下标错位这类 bug 页面照样渲染（只是上下文取错），只有拿正文核对才抓得到。
      final miss = items.firstWhere((it) => it['char'] == _missChar);
      final pos = miss['first_pos'] as int;
      expect(pos, _missPos, reason: 'Python 侧算出的位置就是 $_missPos');
      expect(_textBody[pos], _missChar, reason: '正文 $_missPos 处应当是「$_missChar」');
      expect(
        miss['context_before'],
        _textBody.substring(pos - 4 < 0 ? 0 : pos - 4, pos),
        reason: '上下文由服务端从 content_plain 截，必须与正文一致',
      );
      expect(
        miss['context_after'],
        _textBody.substring(pos + 1, pos + 5 > _textBody.length ? _textBody.length : pos + 5),
        reason: '上下文由服务端从 content_plain 截，必须与正文一致',
      );
      // 背了 N 遍、每遍都漏同一个字 → 次数必须累加（不能每遍覆盖成 1）
      expect(miss['wrong_count'], _reciteTimes);
    });

    test('语文「重背这篇」走生产 toRequest()：content_type=chinese + scope=lesson + text_ids，且不发 mode', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (local, api) = await bootStudent();
      final repo = HomeRepository(api: api, store: local);

      // 课文 id 从 §8.10 的响应里拿（页面「重背这篇」也是这么拿的）
      final raw = await api.get<Map<String, dynamic>>('/api/me/wrong-chars');
      final items = (raw!['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(items, isNotEmpty, reason: '没有漏字就没法测「重背这篇」');
      final textId = items.first['text_id'] as int;

      final page = await repo.fetchUnits();
      final unit =
          page.value.items.firstWhere((u) => u.subject == Subject.chinese);

      // **走生产代码的请求体构造** —— 这条用例的价值一半在于 `toRequest()` 的字段名。
      // 语文分支的坑是「多发了 `mode`」：早先塞了 `'recite'`，
      // 被 Pydantic 的枚举拦成 `10001`，**语文练习从 App 一条都发不出去**。
      // 假适配器不校验请求体，单测永远看不见。
      final config = PracticeConfig(
        unit: unit,
        scope: PracticeScope.lesson,
        selectedTextIds: {textId},
      );
      final body = config.toRequest();
      final strategy = body['strategy'] as Map;
      expect(body['content_type'], 'chinese');
      expect(strategy['scope'], 'lesson', reason: '语文重背走 lesson + text_ids，不是 wrong');
      expect(
        strategy.containsKey('mode'),
        isFalse,
        reason: '语文不发 mode（发了会被 Pydantic 拦成 10001，语文一条练习都发不出去）',
      );
      expect((strategy['scope_detail'] as Map)['text_ids'], <int>[textId]);
      expect(strategy['scope'], isNot('wrong'), reason: '语文没有「只背这几个字」模式');

      final data = await api.post<Map<String, dynamic>>('/api/sessions', body: body);
      expect(data, isNotNull, reason: '后端必须接受语文 lesson + text_ids 组卷');
      final paper = (data!['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(paper, isNotEmpty, reason: '组卷应给出课文');
      expect(data['session_id'], isA<int>());
      // 语文 `items` 是**课文清单**（`text_id`/`title`/`recite_scope`），不是词条
      expect(paper.first['text_id'], isA<int>(), reason: '语文 items 是课文清单，不是词条');
      expect(
        paper.map((e) => e['text_id']).contains(textId),
        isTrue,
        reason: '选中的课文必须真出现在卷面里（否则 text_ids 被静默忽略了）',
      );
    });

    test('两个端点互不串台：§8.10 只回「课文+字」，§8.8 只回「词条」', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api) = await bootStudent();
      final wc = await api.get<Map<String, dynamic>>('/api/me/wrong-chars');
      final ww = await api.get<Map<String, dynamic>>('/api/me/wrong-words');

      final charItem = (wc!['items'] as List).first as Map<String, dynamic>;
      final wordItem = (ww!['items'] as List).first as Map<String, dynamic>;

      // 字段集合**没有一个是重合的** —— 这正是「不合并成同一端点」的理由，
      // 合成一份 items 后客户端就得按 subject 分叉解析，是接口最容易长歪的一类。
      expect(charItem.containsKey('char'), isTrue);
      expect(charItem.containsKey('en'), isFalse);
      expect(wordItem.containsKey('en'), isTrue);
      expect(wordItem.containsKey('char'), isFalse);
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
