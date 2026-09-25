/// WP-APP-01 网络层 —— 解包规则、中文错误、token、重试策略（U14 / U15）。
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/net/api_client.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';

/// 用假的 HTTP 适配器替掉真网络。
class _FakeAdapter implements HttpClientAdapter {
  _FakeAdapter(this.handler);

  final Future<ResponseBody> Function(RequestOptions options) handler;
  final List<RequestOptions> requests = [];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) {
    requests.add(options);
    return handler(options);
  }

  @override
  void close({bool force = false}) {}
}

ResponseBody _json(Map<String, dynamic> payload, {int status = 200}) =>
    ResponseBody.fromString(
      jsonEncode(payload),
      status,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    SharedPreferences.setMockInitialValues({
      'auth.token': 'test-token',
      'server.host': '192.168.1.80',
      'server.port': 8810,
    });
    local = await LocalStore.open();
    serverStore = ServerStore(local);
  });

  Future<ApiClient> clientWith(_FakeAdapter adapter) async {
    final dio = Dio()..httpClientAdapter = adapter;
    return ApiClient(serverStore: serverStore, localStore: local, dio: dio);
  }

  group('响应解包（契约 §3：HTTP 200 + code 判成败）', () {
    test('code = 0 → 返回 data', () async {
      final adapter = _FakeAdapter((_) async => _json({
            'code': 0,
            'message': 'ok',
            'data': {'service': 'bjsys', 'version': '1.0.0'},
            'trace_id': 'abc123',
          }));
      final api = await clientWith(adapter);
      final data = await api.get<Map<String, dynamic>>('/api/ping');
      expect(data?['service'], 'bjsys');
    });

    test('自动带 Bearer token', () async {
      final adapter = _FakeAdapter((_) async => _json({'code': 0, 'data': null}));
      final api = await clientWith(adapter);
      await api.get<dynamic>('/api/auth/me');
      expect(adapter.requests.single.headers['Authorization'], 'Bearer test-token');
    });

    test('skipAuth 不带 token（登录/注册/探活）', () async {
      final adapter = _FakeAdapter((_) async => _json({'code': 0, 'data': null}));
      final api = await clientWith(adapter);
      await api.post<dynamic>(
        '/api/auth/login',
        body: {'username': 'a', 'password': 'b', 'role': 'student'},
        override: const RequestOptionsOverride(skipAuth: true),
      );
      expect(adapter.requests.single.headers.containsKey('Authorization'), isFalse);
    });

    test('查询参数与路径拼接正确', () async {
      final adapter = _FakeAdapter((_) async => _json({'code': 0, 'data': null}));
      final api = await clientWith(adapter);
      await api.get<dynamic>('/api/classes', query: {'grade': 3});
      expect(adapter.requests.single.uri.toString(),
          'http://192.168.1.80:8810/api/classes?grade=3');
    });

    test('page 结构取 items', () async {
      final adapter = _FakeAdapter((_) async => _json({
            'code': 0,
            'data': {
              'items': [
                {'id': 1},
                {'id': 2},
              ],
              'total': 2,
              'page': 1,
              'page_size': 20,
            },
          }));
      final api = await clientWith(adapter);
      final items = await api.list('/api/units');
      expect(items.length, 2);
    });

    test('items 不是列表时返回空表，不崩', () async {
      final adapter = _FakeAdapter((_) async => _json({'code': 0, 'data': {}}));
      final api = await clientWith(adapter);
      expect(await api.list('/api/units'), isEmpty);
    });
  });

  group('业务失败 → 中文异常（U14）', () {
    test('code != 0 抛 ApiException，message 用后端给的中文', () async {
      final adapter = _FakeAdapter((_) async => _json({
            'code': 20001,
            'message': '该学号已被注册，请直接登录',
            'data': null,
            'trace_id': 'f3a9c1',
          }));
      final api = await clientWith(adapter);
      await expectLater(
        api.post<dynamic>('/api/auth/register', body: {}),
        throwsA(
          isA<ApiException>()
              .having((e) => e.code, 'code', 20001)
              .having((e) => e.kind, 'kind', ApiErrorKind.business)
              .having((e) => e.message, 'message', contains('已被注册'))
              .having((e) => e.traceId, 'traceId', 'f3a9c1'),
        ),
      );
    });

    test('displayText 带 trace_id 编号，且不含任何英文堆栈', () async {
      final adapter = _FakeAdapter((_) async => _json({
            'code': 30002,
            'message': '内容不存在',
            'trace_id': 'abc12345',
          }));
      final api = await clientWith(adapter);
      try {
        await api.get<dynamic>('/api/texts/9');
        fail('应该抛异常');
      } on ApiException catch (e) {
        expect(e.displayText, '内容不存在（编号 abc12345）');
        expect(e.displayText, isNot(contains('Exception')));
        expect(e.displayText, isNot(contains('HTTP')));
      }
    });

    test('没有 trace_id 时不显示空的编号括号', () {
      final e = ApiException.business(30002, '内容不存在');
      expect(e.displayText, '内容不存在');
    });

    test('10002 → unauthorized，并触发 onUnauthorized 钩子（U8）', () async {
      final adapter = _FakeAdapter((_) async => _json({
            'code': 10002,
            'message': '登录已过期，请重新登录',
          }));
      final api = await clientWith(adapter);
      var notified = 0;
      api.onUnauthorized = () => notified++;
      await expectLater(
        api.get<dynamic>('/api/auth/me'),
        throwsA(isA<ApiException>().having((e) => e.isUnauthorized, '过期', isTrue)),
      );
      expect(notified, 1);
    });
  });

  group('网络异常 → 中文文案（U15）', () {
    test('连不上：给出可操作的中文提示', () async {
      final adapter = _FakeAdapter((options) async {
        throw DioException(
          requestOptions: options,
          type: DioExceptionType.connectionError,
          error: 'SocketException: Connection refused',
        );
      });
      final api = await clientWith(adapter);
      await expectLater(
        api.get<dynamic>('/api/units'),
        throwsA(
          isA<ApiException>()
              .having((e) => e.kind, 'kind', ApiErrorKind.network)
              .having((e) => e.displayText, '文案', '连不上服务器，看看是不是换了网络'),
        ),
      );
    });

    test('超时：文案友好', () async {
      final adapter = _FakeAdapter((options) async {
        throw DioException(
          requestOptions: options,
          type: DioExceptionType.receiveTimeout,
        );
      });
      final api = await clientWith(adapter);
      try {
        await api.post<dynamic>('/api/units', body: {});
        fail('应该抛异常');
      } on ApiException catch (e) {
        expect(e.kind, ApiErrorKind.timeout);
        expect(e.message, '网络有点慢，请稍后再试');
      }
    });

    test('服务端 500 且返回 HTML → 仍然只说中文', () async {
      final adapter = _FakeAdapter((_) async => ResponseBody.fromString(
            '<html><body>502 Bad Gateway</body></html>',
            502,
            headers: {
              Headers.contentTypeHeader: ['text/html'],
            },
          ));
      final api = await clientWith(adapter);
      try {
        await api.get<dynamic>('/api/units');
        fail('应该抛异常');
      } on ApiException catch (e) {
        expect(e.kind, ApiErrorKind.server);
        expect(e.displayText, '服务器忙，请稍后再试');
        expect(e.displayText, isNot(contains('502')));
        expect(e.displayText, isNot(contains('html')));
      }
    });

    test('HTTP 401 → 按登录过期处理', () async {
      final adapter = _FakeAdapter((_) async => _json({'detail': 'unauthorized'}, status: 401));
      final api = await clientWith(adapter);
      try {
        await api.get<dynamic>('/api/me/profile');
        fail('应该抛异常');
      } on ApiException catch (e) {
        expect(e.isUnauthorized, isTrue);
      }
    });

    test('没配服务器地址 → notReady，而不是发一个空 URL', () async {
      SharedPreferences.setMockInitialValues({});
      final blankLocal = await LocalStore.open();
      final blankServer = ServerStore(blankLocal);
      final api = ApiClient(
        serverStore: blankServer,
        localStore: blankLocal,
        dio: Dio()..httpClientAdapter = _FakeAdapter((_) async => _json({'code': 0})),
      );
      await expectLater(
        api.get<dynamic>('/api/units'),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', ApiErrorKind.notReady)),
      );
    });
  });

  group('重试策略：只重试幂等 GET', () {
    test('GET 网络错误会重试一次', () async {
      var calls = 0;
      final adapter = _FakeAdapter((options) async {
        calls++;
        throw DioException(
          requestOptions: options,
          type: DioExceptionType.connectionError,
        );
      });
      final api = await clientWith(adapter);
      await expectLater(api.get<dynamic>('/api/units'), throwsA(isA<ApiException>()));
      expect(calls, 2, reason: 'GET 应该重试一次');
    });

    test('POST 绝不重试（防重复注册/重复交卷）', () async {
      var calls = 0;
      final adapter = _FakeAdapter((options) async {
        calls++;
        throw DioException(
          requestOptions: options,
          type: DioExceptionType.connectionError,
        );
      });
      final api = await clientWith(adapter);
      await expectLater(
        api.post<dynamic>('/api/auth/register', body: {}),
        throwsA(isA<ApiException>()),
      );
      expect(calls, 1, reason: 'POST 重试会造成重复数据');
    });

    test('业务错误不重试', () async {
      var calls = 0;
      final adapter = _FakeAdapter((_) async {
        calls++;
        return _json({'code': 30002, 'message': '内容不存在'});
      });
      final api = await clientWith(adapter);
      await expectLater(api.get<dynamic>('/api/texts/9'), throwsA(isA<ApiException>()));
      expect(calls, 1);
    });
  });

  group('探活 ping（F5 / 接口设计 §3.1）', () {
    test('服务正常时解析出服务信息', () async {
      final adapter = _FakeAdapter((_) async => _json({
            'code': 0,
            'data': {
              'service': 'bjsys',
              'version': '1.0.0',
              'server_name': '背记系统',
            },
          }));
      final api = await clientWith(adapter);
      final info = await api.ping();
      expect(info?.service, 'bjsys');
      expect(info?.serverName, '背记系统');
    });

    test('探活失败返回 null，**不抛异常**（它在启动路径上，失败是常态）', () async {
      final adapter = _FakeAdapter((options) async {
        throw DioException(
          requestOptions: options,
          type: DioExceptionType.connectionError,
        );
      });
      final api = await clientWith(adapter);
      expect(await api.ping(), isNull);
    });

    test('探活失败重试后仍失败也是 null', () async {
      final adapter = _FakeAdapter((_) async => ResponseBody.fromString('', 500));
      final api = await clientWith(adapter);
      expect(await api.ping(), isNull);
    });

    test('探活走 skipAuth（服务器都没认出来，不该带 token）', () async {
      final adapter = _FakeAdapter((_) async => _json({'code': 0, 'data': {}}));
      final api = await clientWith(adapter);
      await api.ping();
      expect(adapter.requests.single.headers.containsKey('Authorization'), isFalse);
    });

    test('可以临时指定地址（服务发现页「测试连接」用）', () async {
      final adapter = _FakeAdapter((_) async => _json({
            'code': 0,
            'data': {'service': 'bjsys'},
          }));
      final api = await clientWith(adapter);
      final info = await api.ping(
        base: const ServerAddress(host: '10.0.0.9', port: 9000),
      );
      expect(info, isNotNull);
      expect(adapter.requests.single.uri.host, '10.0.0.9');
      expect(adapter.requests.single.uri.port, 9000);
      // 全局地址不应被改掉
      expect(serverStore.current?.host, '192.168.1.80');
    });
  });

  group('ApiException 语义', () {
    test('取消类不该弹提示', () {
      expect(ApiException.cancelled().shouldNotify, isFalse);
      expect(ApiException.network().shouldNotify, isTrue);
    });

    test('toString 只给开发者看，含技术字段', () {
      final e = ApiException.business(20001, '学号重复', traceId: 't1');
      expect(e.toString(), contains('20001'));
      expect(e.toString(), contains('t1'));
    });
  });
}
