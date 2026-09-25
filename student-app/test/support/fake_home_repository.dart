/// APP-02 测试共用的假件 —— 不是测试文件（文件名不以 `_test.dart` 结尾，
/// `flutter test` 不会把它当用例跑）。
///
/// **为什么要有这一层**：两个测试文件如果各写一份假仓库，
/// 将来 `HomeRepository` 加一个方法，很可能只补了一处，
/// 另一处开始悄悄抛 `NoSuchMethodError`，报错信息还指不到真正的原因。
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/discovery/broadcast_finder.dart';
import 'package:student_app/core/net/api_client.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/features/home/home_repository.dart';
import 'package:student_app/features/home/models.dart';

/// 不真的发 UDP 的广播发现 —— 真实现会广播 3 轮、每轮等 2 秒，
/// 在测试里表现出来就是"pumpAndSettle 永远等不完 + Timer 未清理"。
class SilentBroadcastFinder implements BroadcastFinder {
  @override
  final bool supported = true;

  @override
  String? get lastError => null;

  @override
  Future<ServerAddress?> find({
    Duration timeout = kDiscoverTimeout,
    int retries = kDiscoverRetries,
    void Function(String status)? onStatus,
  }) async {
    onStatus?.call('正在寻找服务器…');
    return null;
  }

  @override
  Future<void> dispose() async {}
}

/// 测试默认视口只有 800×600，而首页/配置页都是长列表 ——
/// 超出视口的条目**根本不会被构建**，`find.text('15 秒/题')` 会找不到，
/// 报成"文本不存在"这种容易误导人的错。
///
/// 把视口调高比"每处都 scrollUntilVisible"更省事，也更贴近真机（手机屏幕本来就长）。
void useTallScreen(WidgetTester tester) {
  tester.view.physicalSize = const Size(1200, 3600);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
}

/// 假仓库：只实现首页/配置页真正会调的方法。
///
/// 声明了 `noSuchMethod` 之后，Dart 不再要求逐个实现接口成员 ——
/// 但**用到没实现的方法时会抛 `NoSuchMethodError`**，
/// 这正是我们想要的：测试会立刻炸，而不是静默返回空数据。
class FakeHomeRepository implements HomeRepository {
  FakeHomeRepository({
    this.units,
    this.progress,
    this.texts,
    this.words,
    this.wrongCount = 0,
    this.failUnitsWith,
  });

  /// 字段公开是为了能直接用 `this.xxx` 做形参初始化 ——
  /// 私有字段 + 公开具名形参是合并不了的（具名形参不允许以下划线开头）。
  final UnitsPage? units;
  final ProgressOverview? progress;
  final List<TextBrief>? texts;
  final List<WordItem>? words;

  final int wrongCount;

  /// 非空时 `fetchUnits` 抛这个异常（模拟断网 / 服务端出错）。
  final ApiException? failUnitsWith;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);

  @override
  ApiClient get api => throw UnimplementedError();

  @override
  LocalStore get store => throw UnimplementedError();

  @override
  Future<Cached<UnitsPage>> fetchUnits({Subject? subject, int? grade, int? semester}) async {
    final failure = failUnitsWith;
    if (failure != null) {
      throw failure;
    }
    return Cached(units ?? const UnitsPage(items: []));
  }

  @override
  Future<Cached<ProgressOverview>> fetchProgress() async =>
      Cached(progress ?? const ProgressOverview());

  @override
  Future<int> fetchWrongCount() async => wrongCount;

  @override
  Future<List<TextBrief>> fetchTexts(int unitId) async => texts ?? const [];

  @override
  Future<List<WordItem>> fetchWords(int unitId, {String? only, String? mode}) async =>
      words ?? const [];

  @override
  Future<bool> flushPendingPreference() async => false;

  @override
  bool hasPendingPreference() => false;

  @override
  Future<bool> syncPreference({required int grade, required int semester}) async => true;
}

/// 把 `dio` 的传输层换成"直接回一段 JSON"，从而**不碰真网络**。
///
/// 为什么不自己造一个假 `ApiClient`：那样会把"解包 / 判 code / 抛中文异常"
/// 这一层也一起假掉，而正是这一层最容易出错（APP-01 在这里踩过坑）。
/// 只换 adapter 的话，`ApiClient` 走的仍是生产代码路径。
class JsonAdapter implements HttpClientAdapter {
  JsonAdapter(this.handler);

  /// 传入请求，返回**响应体字符串**。
  final String Function(RequestOptions options) handler;

  /// 收到过的请求（断言请求体用）。
  final List<RequestOptions> received = [];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    received.add(options);
    return ResponseBody.fromString(
      handler(options),
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

/// 包一层契约响应（`{code, message, data, trace_id}`）。
String okBody(Object? data) => jsonEncode({
      'code': 0,
      'message': 'ok',
      'data': data,
      'trace_id': 'test-trace',
    });

/// 构造一个走假 adapter 的 `ApiClient`（其余参数用测试里的真 store）。
ApiClient clientWith(
  JsonAdapter adapter, {
  required ServerStore serverStore,
  required LocalStore localStore,
}) =>
    ApiClient(
      serverStore: serverStore,
      localStore: localStore,
      dio: Dio(
        BaseOptions(
          connectTimeout: const Duration(seconds: 2),
          receiveTimeout: const Duration(seconds: 2),
          validateStatus: (status) => status != null && status < 600,
        ),
      )..httpClientAdapter = adapter,
    );
