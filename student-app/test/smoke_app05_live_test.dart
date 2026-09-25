/// WP-APP-05 **真后端联调** —— 打真实 uvicorn，验证错题本 / 我的记录·统计的数据契约。
///
/// 为什么非要这一层：`test/test_app05_*.dart` 喂的是**手写 JSON**，只能证明
/// "我按自己的想象解析响应"。下面这些才是假数据测不出来的（APP-05 专属）：
/// - `/api/me/wrong-words` 的字段到底叫 `word_id` / `wrong_count` / `en` / `cn`
///   （错题本页 `WrongWord` 模型直接读这些字段）；
/// - `scope=wrong` 组卷走**生产 `PracticeConfig.toRequest()`**，后端认不认这个字段名
///   （`StrategyIn` 是 `extra="ignore"`，拼错不报错、静默按默认出题，最阴的坑）；
/// - `/api/me/progress` 的字段齐全且都是数字（我的记录 / 统计页直接拼字符串）。
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

  setUpAll(() async {
    SharedPreferences.setMockInitialValues({});
    final local = await LocalStore.open();
    final store = ServerStore(local);
    final api = ApiClient(serverStore: store, localStore: local);
    final pong = await api.ping(
      base: ServerAddress(host: _host, port: _port),
      timeout: const Duration(seconds: 3),
    );
    backendUp = pong != null;
    if (!backendUp) {
      // ignore: avoid_print
      print('⚠️  后端 $_host:$_port 未启动，真机联调用例已跳过。');
    }
  });

  /// 登录 Python 造好的烟测学生 —— 走生产路径（ProviderContainer + AuthController），
  /// 保证 token 真的落在 LocalStore 里，后续 `api.get` 自动带 Authorization。
  Future<(LocalStore, ApiClient)> bootStudent() async {
    SharedPreferences.setMockInitialValues({});
    final local = await LocalStore.open();
    final container = ProviderContainer(
      overrides: [localStoreProvider.overrideWithValue(local)],
    );
    addTearDown(container.dispose);
    await container
        .read(serverStoreProvider)
        .save(ServerAddress(host: _host, port: _port));
    final api = container.read(apiClientProvider);
    await container
        .read(authControllerProvider.notifier)
        .login(username: _user, password: _pass);
    return (local, api);
  }

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
      final (local, _) = await bootStudent();
      expect(local.token, isNotNull, reason: '登录态应落在 LocalStore');
      expect(local.token!.isNotEmpty, isTrue);
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
}
