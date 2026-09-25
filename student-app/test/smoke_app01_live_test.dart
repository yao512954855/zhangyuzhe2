/// WP-APP-01 **真后端联调** —— 打真实 uvicorn，验证客户端与后端契约一致。
///
/// 为什么非要这一层：前面 `test_app01_net_test.dart` 用的是**假 HTTP 适配器**，
/// 它只能证明"我按自己的想象解析响应"。字段名写错（`access_token` 写成 `token`）、
/// 错误码对不上、响应包装形状理解偏差，**全部测不出来**。
/// 这一层跑真 HTTP，是唯一能发现这类问题的。
///
/// 后端没起时**自动跳过**（不算失败）—— 否则 `flutter test` 全量跑会莫名其妙红。
///
/// ⚠️ **必须 `HttpOverrides.global = null`**：`TestWidgetsFlutterBinding` 默认会装一个
/// 全局 `HttpOverrides`，把**任何**真实 HTTP 请求拦成 `400 + 空响应体`（框架防止单测
/// 偷偷打网络的保护）。不解开的话，这一层会「看起来全绿」——实际全是 skip，
/// 一个真请求都没发出去过。
///
/// 由 `scripts/smoke_app01.py` 自动拉起后端后执行。
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

/// 真后端地址（与 `backend/` 的启动脚本一致；可由 smoke 脚本用 --dart-define 覆盖）。
const _host = String.fromEnvironment('APP01_SMOKE_HOST', defaultValue: '127.0.0.1');
const _port = int.fromEnvironment('APP01_SMOKE_PORT', defaultValue: 8810);

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

  Future<(LocalStore, ServerStore, ApiClient)> freshClient() async {
    SharedPreferences.setMockInitialValues({});
    final local = await LocalStore.open();
    final store = ServerStore(local);
    await store.save(const ServerAddress(host: _host, port: _port));
    return (local, store, ApiClient(serverStore: store, localStore: local));
  }

  String uniqueUsername() =>
      'A${DateTime.now().millisecondsSinceEpoch % 100000000}';

  /// 走**生产路径**的一整套：LocalStore + ProviderContainer + ApiClient + AuthController。
  ///
  /// ⚠️ 别再用裸 `ApiClient` 断言「token 已落库」—— 落库是 `AuthController` 的职责，
  /// 裸 client 只负责解包 `{code,message,data}`。早先就是这么写错的，
  /// 结果两条用例假红（token 为 null）。
  Future<(LocalStore, ProviderContainer, ApiClient, AuthController)>
      freshSession() async {
    SharedPreferences.setMockInitialValues({});
    final local = await LocalStore.open();
    final container = ProviderContainer(
      overrides: [localStoreProvider.overrideWithValue(local)],
    );
    addTearDown(container.dispose);
    await container
        .read(serverStoreProvider)
        .save(const ServerAddress(host: _host, port: _port));
    return (
      local,
      container,
      container.read(apiClientProvider),
      container.read(authControllerProvider.notifier),
    );
  }

  group('真后端 · 服务发现与鉴权', () {
    test('探活返回契约字段（接口设计 §3.1）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, api) = await freshClient();
      final info = await api.ping();
      expect(info, isNotNull);
      expect(info!.service, 'bjsys');
      expect(info.serverName, isNotEmpty);
      expect(info.version, isNotEmpty);
    });

    test('注册 → 拿到 access_token 与 user（契约 §5.2 字段名）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (local, _, api) = await freshClient();
      final username = uniqueUsername();

      final data = await api.post<Map<String, dynamic>>(
        '/api/auth/register',
        body: {
          'username': username,
          'real_name': '联调小同学',
          'password': '123456',
        },
        override: const RequestOptionsOverride(skipAuth: true),
      );

      expect(data, isNotNull);
      // ⚠️ 字段名是 `access_token`，不是 `token`（契约 > 接口设计的优先级裁决）
      expect(data!['access_token'], isA<String>());
      expect((data['access_token'] as String).isNotEmpty, isTrue);
      expect(data['token_type'], 'Bearer');
      expect(data['expires_in'], isA<int>());

      final user = data['user'] as Map<String, dynamic>;
      expect(user['username'], username);
      expect(user['real_name'], '联调小同学');
      expect(user['role'], 'student');
      // 注意：这里**只验契约字段名**。「token 是否落库」是 AuthController 的职责，
      // 由下一条用例覆盖（裸 client 不会写本地存储）。
    });

    test('注册走生产路径（AuthController）→ token 落库、restore 能认出自己', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (local, _, _, auth) = await freshSession();
      final username = uniqueUsername();

      final user = await auth.register(
        username: username,
        realName: '联调同学',
        password: '123456',
      );
      expect(user.username, username);
      expect(user.realName, '联调同学');

      // ① 落库（否则换个页面/重启就"没登录"）
      expect(local.token, isNotNull);
      expect(local.token!.isNotEmpty, isTrue);
      expect(local.userId, user.id);
      expect(local.username, username);
      expect(local.classId, isNull, reason: '注册时没选班级 → class_id 应为 null');
      expect(auth.state.isLoggedIn, isTrue);

      // ② 启动校验能认出自己（真打 /api/auth/me）
      final restored = await auth.restore();
      expect(restored, isTrue);
      expect(auth.state.user?.username, username);
      expect(auth.state.offline, isFalse);
    });

    test('学号重复 → 20001，文案可直接上屏（U6）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, api) = await freshClient();
      final username = uniqueUsername();
      Future<void> register() => api.post<Map<String, dynamic>>(
            '/api/auth/register',
            body: {'username': username, 'real_name': '同学', 'password': '123456'},
            override: const RequestOptionsOverride(skipAuth: true),
          );
      await register();

      try {
        await register();
        fail('重复注册应该失败');
      } on ApiException catch (e) {
        expect(e.code, 20001);
        expect(e.kind, ApiErrorKind.business);
        // 中文、无英文堆栈
        expect(RegExp(r'[\u4e00-\u9fa5]').hasMatch(e.message), isTrue);
        expect(e.displayText.contains('Exception'), isFalse);
      }
    });

    test('密码错误 → 20002，且文案不泄露"学号是否存在"（防枚举）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, api) = await freshClient();
      final username = uniqueUsername();
      await api.post<Map<String, dynamic>>(
        '/api/auth/register',
        body: {'username': username, 'real_name': '同学', 'password': '123456'},
        override: const RequestOptionsOverride(skipAuth: true),
      );

      Future<ApiException> loginAs(String u, String p) async {
        try {
          await api.post<Map<String, dynamic>>(
            '/api/auth/login',
            body: {'username': u, 'password': p, 'role': 'student'},
            override: const RequestOptionsOverride(skipAuth: true),
          );
          fail('应该失败');
        } on ApiException catch (e) {
          return e;
        }
      }

      final wrongPassword = await loginAs(username, '654321');
      final noSuchUser = await loginAs('Z${uniqueUsername().substring(1)}', '123456');

      expect(wrongPassword.code, 20002);
      expect(noSuchUser.code, 20002);
      expect(
        wrongPassword.message,
        noSuchUser.message,
        reason: '两条路径文案必须完全一致，否则能被用来枚举学号',
      );
    });

    test('正确登录拿到新 token', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, api) = await freshClient();
      final username = uniqueUsername();
      await api.post<Map<String, dynamic>>(
        '/api/auth/register',
        body: {'username': username, 'real_name': '同学', 'password': 'abc123'},
        override: const RequestOptionsOverride(skipAuth: true),
      );

      final data = await api.post<Map<String, dynamic>>(
        '/api/auth/login',
        body: {'username': username, 'password': 'abc123', 'role': 'student'},
        override: const RequestOptionsOverride(skipAuth: true),
      );
      expect(data?['access_token'], isA<String>());
    });

    test('管理端角色登录学生入口 → 也是 20002（隔离两端入口）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, api) = await freshClient();
      try {
        await api.post<Map<String, dynamic>>(
          '/api/auth/login',
          body: {'username': 'admin', 'password': 'admin123', 'role': 'student'},
          override: const RequestOptionsOverride(skipAuth: true),
        );
        // 如果初始管理员密码不是 admin123，这条会以 20002 抛出，同样符合预期
      } on ApiException catch (e) {
        expect(e.code, 20002, reason: '角色不匹配也要走同一个错误码');
      }
    });

    test('无效 token → 10002，且触发登录过期钩子（U8）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (local, _, api) = await freshClient();
      await local.saveAuth(
        token: 'definitely-not-a-valid-token',
        userId: 1,
        username: 'nobody',
      );
      var notified = 0;
      api.onUnauthorized = () => notified++;

      try {
        await api.get<Map<String, dynamic>>('/api/auth/me');
        fail('应该失败');
      } on ApiException catch (e) {
        expect(e.isUnauthorized, isTrue);
        expect(e.code, 10002);
      }
      expect(notified, 1, reason: '过期钩子必须被触发，否则 App 不会跳登录页');
    });

    test('探活走的地址可以不带 token', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (local, _, api) = await freshClient();
      await local.saveAuth(token: 'garbage', userId: 1, username: 'x');
      // 探活失败才说明它没被鉴权拦住
      expect(await api.ping(), isNotNull);
    });
  });

  group('真后端 · 错误码与响应形状', () {
    test('不存在的路径 → 404 也不会漏出英文给用户', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, api) = await freshClient();
      try {
        await api.get<Map<String, dynamic>>('/api/definitely-not-here');
        fail('应该失败');
      } on ApiException catch (e) {
        expect(e.displayText.contains('Exception'), isFalse);
        expect(RegExp(r'[\u4e00-\u9fa5]').hasMatch(e.displayText), isTrue);
      }
    });

    test('带数字查询参数不会崩（客户端曾在这里踩过坑）', () async {
      if (!backendUp) {
        markTestSkipped('后端未启动');
        return;
      }
      final (_, _, api) = await freshClient();
      // 未登录 → 预期业务上是失败，但**不能是客户端自己拼 URL 拼崩**
      try {
        await api.get<Map<String, dynamic>>('/api/classes', query: {'grade': 3});
      } on ApiException catch (e) {
        expect(e.kind, isNot(ApiErrorKind.parse));
      }
    });
  });
}
