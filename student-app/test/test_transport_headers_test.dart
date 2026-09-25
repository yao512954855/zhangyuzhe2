/// `ApiClient` 自定义请求头 —— 为 APP-03 F15 的 `Idempotency-Key` 而加的能力。
///
/// 【为什么需要这个文件】
/// F15 / 契约 §9.2 要求提交答案带 `Idempotency-Key` 防重，但原先
/// `RequestOptionsOverride` 只支持 base/skipAuth/timeout，**没有任何办法附加请求头**，
/// 于是 APP-03 只能把「生成幂等键」的逻辑写出来却发不出去（记为 D-APP03-01）。
/// 收口时给 `RequestOptionsOverride` 加了 `headers`（默认 `null`）。
///
/// ⚠️ 这个改动**必须有一条真请求的用例**才算验过：光看 `flutter analyze` 通过，
/// 只能说明它编译得过 —— 头有没有真的进 `RequestOptions`、有没有被后面覆盖掉，
/// 编译期一概看不出来（这正是本项目反复踩的「假绿」）。
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/net/api_client.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';

/// 假适配器：只记录 `RequestOptions`，不碰真网络。
class _RecordingAdapter implements HttpClientAdapter {
  final List<RequestOptions> requests = [];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);
    return ResponseBody.fromString(
      jsonEncode({'code': 0, 'message': 'ok', 'data': {'ok': true}, 'trace_id': 't'}),
      200,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;
  late _RecordingAdapter adapter;
  late ApiClient api;

  setUp(() async {
    SharedPreferences.setMockInitialValues({
      'auth.token': 'test-token',
      'server.host': '192.168.1.80',
      'server.port': 8810,
    });
    local = await LocalStore.open();
    serverStore = ServerStore(local);
    adapter = _RecordingAdapter();
    api = ApiClient(
      serverStore: serverStore,
      localStore: local,
      dio: Dio()..httpClientAdapter = adapter,
    );
  });

  test('不传 headers 时请求头保持原样（零行为变化）', () async {
    await api.get<Map<String, dynamic>>('/api/ping', override: const RequestOptionsOverride(skipAuth: true));
    expect(adapter.requests, hasLength(1));
    final headers = adapter.requests.single.headers;
    expect(headers.containsKey('Idempotency-Key'), isFalse);
    expect(headers.containsKey('Authorization'), isFalse);
  });

  test('headers 里的自定义头真的进了请求（Idempotency-Key）', () async {
    await api.post<Map<String, dynamic>>(
      '/api/sessions/7/answers',
      body: {'word_id': 1},
      override: const RequestOptionsOverride(headers: {'Idempotency-Key': 'app03-7-1-123'}),
    );
    expect(adapter.requests, hasLength(1));
    final headers = adapter.requests.single.headers;
    expect(headers['Idempotency-Key'], 'app03-7-1-123');
    // 附加头不能把鉴权顶掉
    expect(headers['Authorization'], 'Bearer test-token');
  });

  test('headers 覆盖不了 Authorization（附加头先加、鉴权后加）', () async {
    await api.get<Map<String, dynamic>>(
      '/api/me/units',
      override: const RequestOptionsOverride(headers: {'Authorization': 'Bearer forged'}),
    );
    expect(adapter.requests.single.headers['Authorization'], 'Bearer test-token');
  });
}
