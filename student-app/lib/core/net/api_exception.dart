/// 接口异常 —— 30-APP-01 F10（U14 / U15 的落点）。
///
/// **设计底线：绝不让英文技术信息上屏。**
/// 学生看到的永远是 `message`（中文、口语化）：
/// 断网时说「连不上服务器，看看是不是换了网络」，而不是 `SocketException: ...`。
/// 原始异常留在 [cause] 里只给日志用。
library;

/// 错误大类。调用方按这个决定"该重试 / 该重新登录 / 该提示用户"。
enum ApiErrorKind {
  /// 连不上（DNS/路由/服务没起）。
  network,

  /// 超时。
  timeout,

  /// 服务端 5xx 或返回了看不懂的东西。
  server,

  /// 业务码非 0（HTTP 200）。
  business,

  /// 登录态失效（HTTP 401 / 业务码 10002）。
  unauthorized,

  /// 还没配置服务器地址，或地址被清掉了。
  notReady,

  /// 响应不是预期的 JSON 结构。
  parse,

  /// 请求被主动取消（页面退出等，**不该提示用户**）。
  cancelled,
}

/// 统一的接口异常。
class ApiException implements Exception {
  ApiException({
    required this.kind,
    required this.message,
    this.code = 0,
    this.traceId,
    this.httpStatus,
    this.cause,
  });

  /// 网络连不上（**给学生的原话**，不含技术术语）。
  factory ApiException.network([Object? cause]) => ApiException(
        kind: ApiErrorKind.network,
        message: '连不上服务器，看看是不是换了网络',
        cause: cause,
      );

  /// 超时。
  factory ApiException.timeout([Object? cause]) => ApiException(
        kind: ApiErrorKind.timeout,
        message: '网络有点慢，请稍后再试',
        cause: cause,
      );

  /// 服务端问题。
  factory ApiException.server([Object? cause, int? httpStatus]) => ApiException(
        kind: ApiErrorKind.server,
        message: '服务器忙，请稍后再试',
        httpStatus: httpStatus,
        cause: cause,
      );

  /// 业务失败（用后端给的中文 message，**它比客户端猜的准**）。
  factory ApiException.business(int code, String message, {String? traceId}) =>
      ApiException(
        kind: code == codeUnauthorized ? ApiErrorKind.unauthorized : ApiErrorKind.business,
        code: code,
        message: message.isEmpty ? '操作没有成功，请再试一次' : message,
        traceId: traceId,
      );

  /// 响应结构不符合契约。
  factory ApiException.parse([Object? cause]) => ApiException(
        kind: ApiErrorKind.parse,
        message: '收到的数据和预期不一样，请再试一次',
        cause: cause,
      );

  /// 尚未配置服务器。
  factory ApiException.notReady() => ApiException(
        kind: ApiErrorKind.notReady,
        message: '还没有连接服务器',
      );

  /// 主动取消。
  factory ApiException.cancelled() => ApiException(
        kind: ApiErrorKind.cancelled,
        message: '请求已取消',
      );

  /// 契约 §3：未认证业务码。
  static const int codeUnauthorized = 10002;

  final ApiErrorKind kind;

  /// 业务码；无则为 0。
  final int code;

  /// **可直接展示的中文文案**。
  final String message;

  /// 服务端 trace_id（排障用，UI 上以「编号 xxx」露出）。
  final String? traceId;

  final int? httpStatus;

  /// 原始异常，仅供日志。
  final Object? cause;

  bool get isUnauthorized =>
      kind == ApiErrorKind.unauthorized || code == codeUnauthorized;

  /// 该不该给学生看提示：取消类不提示。
  bool get shouldNotify => kind != ApiErrorKind.cancelled;

  /// 上屏文案（**带编号**，U14 要求「中文提示含 trace_id」）。
  String get displayText {
    final id = traceId?.trim() ?? '';
    if (id.isEmpty) {
      return message;
    }
    return '$message（编号 $id）';
  }

  @override
  String toString() =>
      'ApiException(kind: $kind, code: $code, message: $message, traceId: $traceId)';
}
