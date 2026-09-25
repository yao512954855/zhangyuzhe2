/// WP-APP-02 **真后端联调** —— 打真实 uvicorn，验证首页/配置页的取数契约。
///
/// 为什么非要这一层：`test_app02_models_test.dart` 里那些用例喂的是**手写 JSON**，
/// 只能证明"我按自己的想象解析响应"。下面这些才是假数据测不出来的：
/// - `/api/me/units` 的语文单元字段到底叫 `text_total` 还是 `text_count`；
/// - 学生视图的词条里会不会漏出 `accepted`（契约禁止清单第 4 条）；
/// - `POST /api/sessions` 的 `strategy` 字段名后端认不认（`StrategyIn` 是
///   `extra="ignore"`，**拼错不报错，静默按默认出题**，最阴的一个坑）；
/// - 断网降级与"业务错误码不许拿缓存糊弄"的分界。
///
/// 后端没起时**自动跳过**（不算失败）—— 否则 `flutter test` 全量跑会莫名其妙红。
///
/// ⚠️ **必须 `HttpOverrides.global = null`**：`TestWidgetsFlutterBinding` 默认会装一个
/// 全局 `HttpOverrides`，把**任何**真实 HTTP 请求拦成 `400 + 空响应体`。
/// 不解开的话，这一层会「看起来全绿」—— 实际全是 skip，一个真请求都没发出去过。
///
/// 由 `scripts/smoke_app02.py` （先起后端 + 造数）自动执行。
library;

import 'dart:io';

import 'package:dio/dio.dart';
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

/// 真后端地址（与 `scripts/smoke_app02.py` 一致；脚本用 --dart-define 覆盖）。
const _host = String.fromEnvironment('APP02_SMOKE_HOST', defaultValue: '127.0.0.1');
const _port = int.fromEnvironment('APP02_SMOKE_PORT', defaultValue: 8812);

/// 造数用的年级学期 —— 必须与 `smoke_app02.py` 的 `SEED_GRADE/SEMESTER` 一致。
const _seedGrade = 3;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  // 解开 flutter_test 的 HTTP 拦截，否则所有真请求都会被吞成 400。
  HttpOverrides.global = null;

  var backendUp = false;

  setUpAll(() async {
    SharedPreferences.setMockInitialValues({});
    final local = await LocalStore.open();
    final store = ServerStore(local);
    final api = ApiClient(serverStore: store, localStore: local);
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

  /// 一个"已注册、但**还没有选年级**"的学生 —— 走生产路径
  /// （ProviderContainer + AuthController），保证 token 真的落在 LocalStore 里。
  ///
  /// `setPreference` 为 true 时顺手 `PUT /api/me/profile`（F3 的服务端那一半）。
  Future<(LocalStore, ApiClient, HomeRepository)> bootStudent({
    bool setPreference = true,
  }) async {
    SharedPreferences.setMockInitialValues({});
    final local = await LocalStore.open();
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

    if (setPreference) {
      await api.put<Map<String, dynamic>>(
        '/api/me/profile',
        body: {'preferred_grade': _seedGrade, 'preferred_semester': 'first'},
      );
    }
    return (local, api, HomeRepository(api: api, store: local));
  }

  /// 占一个端口再放掉 —— 拿到一个**几乎必然没人监听**的端口号，
  /// 用来模拟"服务器联系不上"。比硬写 9 或 1 可靠（那些在少数环境真的会有人听）。
  Future<int> closedPort() async {
    final socket = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    final port = socket.port;
    await socket.close();
    return port;
  }

  /// 指向一个死端口的仓库，但**共用同一个 LocalStore**（token 与缓存都在里面）。
  Future<HomeRepository> offlineRepo(LocalStore local) async {
    final dead = ServerStore(local);
    await dead.save(ServerAddress(host: '127.0.0.1', port: await closedPort()));
    return HomeRepository(
      api: ApiClient(
        serverStore: dead,
        localStore: local,
        // 短超时：不然单条用例要等 10 秒
        dio: Dio(
          BaseOptions(
            connectTimeout: const Duration(milliseconds: 800),
            receiveTimeout: const Duration(milliseconds: 800),
            validateStatus: (status) => status != null && status < 600,
          ),
        ),
      ),
      store: local,
    );
  }

  Future<UnitItem> englishUnit(HomeRepository repo) async {
    final page = await repo.fetchUnits(subject: Subject.english);
    return page.value.items.firstWhere((u) => u.subject == Subject.english);
  }

  Future<UnitItem> chineseUnit(HomeRepository repo) async {
    final page = await repo.fetchUnits(subject: Subject.chinese);
    return page.value.items.firstWhere((u) => u.subject == Subject.chinese);
  }

  // =========================================================== 首页取数
  group('真后端 · 首页取数（F1 / F3 / F5 / U5 / U11）', () {
    test('刚注册还没选年级 → /api/me/units 返回空列表而不是报错（F2 引导页靠它）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, repo) = await bootStudent(setPreference: false);
      final page = await repo.fetchUnits(subject: Subject.english);
      expect(page.value.items, isEmpty);
      expect(page.value.grade, isNull, reason: '没偏好时 grade 应为 null');
      expect(page.fromCache, isFalse);
    });

    test('PUT /api/me/profile 之后按偏好返回内容（F3 双向持久化：服务端那一半）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, repo) = await bootStudent();
      final page = await repo.fetchUnits();
      expect(page.value.grade, _seedGrade);
      expect(page.value.items, isNotEmpty, reason: '造数应能在 3 年级上学期看到内容');
    });

    test('英语单元字段名逐个核对（词条维度）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();
      // 直接看原始 JSON —— 用模型解析过了再看模型，等于自证
      final raw = await api.get<Map<String, dynamic>>(
        '/api/me/units',
        query: {'subject': 'english'},
      );
      final item = (raw!['items'] as List).cast<Map>().first.cast<String, dynamic>();

      for (final key in const [
        'unit_id',
        'subject',
        'name',
        'word_total',
        'practiced',
        'mastered',
        'accuracy',
        'last_practice_at',
        'status',
      ]) {
        expect(item.containsKey(key), isTrue, reason: '§8.3 应有字段 $key');
      }
      expect(item['subject'], 'english');
      expect(item['word_total'], 12, reason: '造数 12 个词条');
      expect(item['practiced'], 0, reason: '新学生还没练过');
      expect(item['accuracy'], 0.0, reason: '没数据时正确率口径为 0.0，不是 null');

      // 模型侧也要能解析出同一个数（防"字段名对但模型没读"）
      final unit = await englishUnit(repo);
      expect(unit.total, 12);
      expect(unit.mastered, 0);
    });

    test('§8.3 **不下发** with_error —— 首页「只练错题」不能按单元猜（偏离 #44 的回归）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();
      final raw = await api.get<Map<String, dynamic>>(
        '/api/me/units',
        query: {'subject': 'english'},
      );
      final item = (raw!['items'] as List).cast<Map>().first.cast<String, dynamic>();
      expect(
        item.containsKey('with_error'),
        isFalse,
        reason: '契约 §8.3 未定义该字段。若后端某天补上了，'
            'app 里的「只练错题」先选单元的兜底流程要同步简化，并且这条用例要改。',
      );
      final unit = await englishUnit(repo);
      expect(unit.withError, isNull, reason: '模型必须如实反映"没拿到"，不许硬凑');
    });

    test('语文单元走课文维度（text_total / recited / last_grade）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();
      final raw = await api.get<Map<String, dynamic>>(
        '/api/me/units',
        query: {'subject': 'chinese'},
      );
      final item = (raw!['items'] as List).cast<Map>().first.cast<String, dynamic>();

      expect(item['subject'], 'chinese');
      expect(item['text_total'], 3, reason: '造数 3 篇课文');
      expect(item['recited'], 0);
      expect(item.containsKey('word_total'), isFalse, reason: '语文不该有词条维度字段');
      expect(item['status'], 'not_started');

      final unit = await chineseUnit(repo);
      expect(unit.total, 3, reason: '模型的 total 对语文要取 text_total');
      expect(unit.lastGrade, isNull);
    });

    test('subject 过滤生效（F4 学科切换的数据侧）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, repo) = await bootStudent();
      final english = await repo.fetchUnits(subject: Subject.english);
      final chinese = await repo.fetchUnits(subject: Subject.chinese);

      expect(english.value.items.map((u) => u.subject).toSet(), {Subject.english});
      expect(chinese.value.items.map((u) => u.subject).toSet(), {Subject.chinese});
      expect(chinese.value.items, isNotEmpty, reason: '语文造数应能查到');
    });

    test('/api/me/progress 字段齐全（首页顶部三个数字）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, repo) = await bootStudent();
      final progress = (await repo.fetchProgress()).value;
      // 新学生：全 0，但**必须都是数字**，不能是 null（UI 直接拼字符串）
      expect(progress.totalWords, 12);
      expect(progress.practicedWords, 0);
      expect(progress.masteredWords, 0);
      expect(progress.streakDays, 0);
      expect(progress.accuracy, 0.0);
    });

    test('缓存时间能带上（U10 断网提示要说清"这是什么时候的数据"）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (local, _, repo) = await bootStudent();
      await repo.fetchUnits(subject: Subject.english);
      final offline = await offlineRepo(local);
      final cached = await offline.fetchUnits(subject: Subject.english);
      expect(cached.fromCache, isTrue);
      expect(cached.cachedAt, isNotNull);
    });
  });

  // =========================================================== 内容与组卷
  group('真后端 · 内容与组卷（F9 / F13 / U7）', () {
    test('/api/units/{id}/words 学生视图**不含 accepted**（契约禁止清单第 4 条）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();
      final unit = await englishUnit(repo);
      final raw = await api.get<Map<String, dynamic>>('/api/units/${unit.unitId}/words');
      final words = (raw!['words'] as List).cast<Map>().cast<Map<String, dynamic>>();

      expect(words, isNotEmpty);
      expect(raw['unit_id'], unit.unitId);
      expect(raw['unit_name'], isNotNull);
      for (final word in words) {
        expect(word.containsKey('accepted'), isFalse, reason: 'accepted 是判分依据，下发等于泄题');
        expect(word.containsKey('status'), isFalse, reason: '学生端查询本就只出 active，返回是噪声');
        expect(word['id'], isA<int>());
        expect(word['mastery'], isA<Map>());
      }
      // 不传 mode 时两侧都要在（单元详情页要完整看）
      final first = words.first;
      expect(first['en'], isNotNull);
      expect(first['cn'], isNotNull);

      final parsed = await repo.fetchWords(unit.unitId);
      expect(parsed.length, 12);
      expect(parsed.every((w) => w.en != null && w.cn != null), isTrue);
    });

    test('/api/units/{id}/texts 学生视图不含正文（只有摘要）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();
      final unit = await chineseUnit(repo);
      final raw = await api.get<Map<String, dynamic>>('/api/units/${unit.unitId}/texts');
      final texts = (raw!['texts'] as List).cast<Map>().cast<Map<String, dynamic>>();

      expect(texts.length, 3);
      final first = texts.first;
      for (final key in const [
        'id',
        'unit_id',
        'title',
        'author',
        'total_chars',
        'recite_scope_chars',
        'paragraph_count',
        'recite_paragraph_count',
      ]) {
        expect(first.containsKey(key), isTrue, reason: '§8.6 应有字段 $key');
      }
      // 学生端不该拿到管理字段
      for (final forbidden in const ['status', 'order_no', 'created_at', 'recite_scope', 'paragraphs', 'content']) {
        expect(first.containsKey(forbidden), isFalse, reason: '学生视图不该有 $forbidden');
      }

      // 背诵范围切片：《背影》只背第 2 段（idx=1），共 2 段
      final backShadow = texts.firstWhere((t) => t['title'] == '背影');
      expect(backShadow['paragraph_count'], 2);
      expect(backShadow['recite_paragraph_count'], 1);
      expect(backShadow['recite_scope_chars'], 4, reason: '「那年冬天」4 个字');
      // 《春》没填背诵范围 → 默认全文两段
      final spring = texts.firstWhere((t) => t['title'] == '春');
      expect(spring['recite_paragraph_count'], 2);

      // ⚠️ **已知的契约与实现口径冲突**（不是 APP-02 能改的，如实钉住）：
      // 契约 §8.6 示例是 `total_chars: 1180, recite_scope_chars: 620`（全文 vs 范围），
      // 但实现里 `texts.total_chars = PinyinService.build_scoped(...).length`
      // —— 是**背诵范围内**的字数，与 `recite_scope_chars` **恒等**。
      // 接口设计 §8.7/§9.4 又写着 `total_chars: 620`（等于范围字数），
      // 所以文档自身就不一致；实现跟的是 §9.4 那一支。
      // 客户端只消费 `recite_scope_chars`（展示与预估都用它），因此不受影响；
      // 若这条断言某天变红，说明后端改成 §8.6 的示例口径了 ——
      // 那时要先确认 §9.4 的 `total_chars` 语义有没有跟着改。
      expect(
        backShadow['total_chars'],
        backShadow['recite_scope_chars'],
        reason: '后端 total_chars 目前 == 背诵范围字数（与 §8.6 示例不符，见上）',
      );

      final parsed = await repo.fetchTexts(unit.unitId);
      expect(parsed.length, 3);
      expect(parsed.first.reciteScopeChars, greaterThan(0));
    });

    test('POST /api/sessions 整单元（英语）→ 题序完整、prompt 不含答案', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();
      final unit = await englishUnit(repo);
      // **走生产代码的请求体构造** —— 这条用例的价值一半在于 `toRequest()` 的字段名
      final config = PracticeConfig(unit: unit);
      final data = await api.post<Map<String, dynamic>>(
        '/api/sessions',
        body: config.toRequest(),
      );

      expect(data, isNotNull);
      expect(data!['unit_id'], unit.unitId);
      expect(data['content_type'], 'english');
      expect(data['session_id'], isA<int>());
      final items = (data['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(items.length, 12, reason: '默认整单元 = 12 个词条（说明 count/count_mode 真的生效了）');
      expect(
        items.map((i) => i['seq']).toList(),
        List<int>.generate(12, (i) => i + 1),
        reason: 'seq 应从 1 连续递增',
      );

      const answerSide = {'en', 'cn', 'accepted', 'expected'};
      for (final item in items) {
        expect(
          item.keys.toSet().intersection(answerSide),
          isEmpty,
          reason: '组卷响应不许带答案侧字段：$item',
        );
        // cn2en：prompt 是中文题面
        expect(
          RegExp(r'[\u4e00-\u9fa5]').hasMatch(item['prompt'].toString()),
          isTrue,
          reason: 'cn2en 模式下题面应是中文，实际：${item['prompt']}',
        );
        expect(item['target_len'] as int, greaterThan(0), reason: '占位框要按答案长度画');
      }
    });

    test('答错一题 → 错题数变 1 → scope=wrong 组出 1 题（首页「只练错题」整条链）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();

      // ① 先确认没练过时是 0
      expect(await repo.fetchWrongCount(), 0);

      // ② 练一遍，故意答错一道
      final unit = await englishUnit(repo);
      final session = await api.post<Map<String, dynamic>>(
        '/api/sessions',
        body: PracticeConfig(unit: unit).toRequest(),
      );
      final item = (session!['items'] as List).cast<Map>().cast<Map<String, dynamic>>()[1];
      await api.post<Map<String, dynamic>>(
        '/api/sessions/${session['session_id']}/answers',
        body: {
          'word_id': item['word_id'],
          'mode': 'cn2en',
          'step': 1,
          'asr_text': 'zzz-not-a-word',
        },
      );

      // ③ 首页那个数字（page_size=1 只取 total）
      expect(await repo.fetchWrongCount(), 1);

      // ④ 「只练错题」组卷：正好 1 题，且就是刚才错的那个词
      final wrong = await api.post<Map<String, dynamic>>(
        '/api/sessions',
        body: PracticeConfig(unit: unit, scope: PracticeScope.wrong).toRequest(),
      );
      final wrongItems = (wrong!['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(wrongItems.length, 1, reason: '错题本里就 1 个词');
      expect(wrongItems.first['word_id'], item['word_id']);
    });

    test('没有错题时 scope=wrong 会被后端明确拒绝（不是静默出整单元）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();
      final unit = await englishUnit(repo);
      try {
        await api.post<Map<String, dynamic>>(
          '/api/sessions',
          body: PracticeConfig(unit: unit, scope: PracticeScope.wrong).toRequest(),
        );
        fail('没错题却组卷成功，说明 scope=wrong 被静默退化了');
      } on ApiException catch (e) {
        expect(e.kind, ApiErrorKind.business);
        expect(RegExp(r'[\u4e00-\u9fa5]').hasMatch(e.displayText), isTrue);
      }
    });

    test('POST /api/sessions 语文 → items 是课文清单（含背诵范围）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();
      final unit = await chineseUnit(repo);
      final data = await api.post<Map<String, dynamic>>(
        '/api/sessions',
        body: PracticeConfig(unit: unit).toRequest(),
      );

      expect(data, isNotNull);
      expect(data!['content_type'], 'chinese');
      final items = (data['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(items.length, 3, reason: '整单元 = 3 篇课文');
      for (final item in items) {
        expect(item['text_id'], isA<int>());
        expect(item['title'], isNotEmpty);
        expect(item['total_chars'], isA<int>());
        expect(item.containsKey('recite_scope'), isTrue, reason: '背诵范围随题下发，App 不用再算');
      }
    });

    test('语文「只背选中的课文」= scope_detail.text_ids，后端认这个字段', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();
      final unit = await chineseUnit(repo);
      final texts = await repo.fetchTexts(unit.unitId);
      final only = texts.first;

      final data = await api.post<Map<String, dynamic>>(
        '/api/sessions',
        body: PracticeConfig(
          unit: unit,
          scope: PracticeScope.lesson,
          selectedTextIds: {only.id},
        ).toRequest(),
      );
      final items = (data!['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(items.length, 1, reason: '只勾了一篇，说明 text_ids 真的被用上了');
      expect(items.first['text_id'], only.id);
    });

    test('英语自定义区间：scope_detail.start/end 被后端用上（不是静默整单元）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, api, repo) = await bootStudent();
      final unit = await englishUnit(repo);
      final data = await api.post<Map<String, dynamic>>(
        '/api/sessions',
        body: PracticeConfig(
          unit: unit,
          scope: PracticeScope.range,
          rangeStart: 2,
          rangeEnd: 6,
        ).toRequest(),
      );
      final items = (data!['items'] as List).cast<Map>().cast<Map<String, dynamic>>();
      expect(items.length, 5, reason: '2–6 共 5 个词条；出 12 个说明 scope_detail 没生效');
    });
  });

  // =========================================================== 断网降级
  group('真后端 · 断网降级（F15 / U10）', () {
    test('网络失败 → 用缓存并标记 fromCache；业务错误码 → 必须抛出去，不许拿缓存糊弄', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (local, _, repo) = await bootStudent();
      final fresh = await repo.fetchUnits(subject: Subject.english);
      expect(fresh.fromCache, isFalse);
      expect(fresh.value.items, isNotEmpty);

      // ① 网络类失败 → 降级读缓存
      final offline = await offlineRepo(local);
      final cached = await offline.fetchUnits(subject: Subject.english);
      expect(cached.fromCache, isTrue);
      expect(cached.value.items.length, fresh.value.items.length);
      expect(cached.cachedAt, isNotNull);

      // ② 业务错误码（10002 登录过期）→ 缓存里明明有数据，也必须抛
      await local.saveAuth(token: 'not-a-real-token', userId: 1, username: 'nobody');
      try {
        await repo.fetchUnits(subject: Subject.english);
        fail('token 失效却从缓存返回了数据 —— 会把"该重新登录"伪装成"一切正常"');
      } on ApiException catch (e) {
        expect(e.code, 10002);
        expect(e.isUnauthorized, isTrue);
      }
    });
  });
}
