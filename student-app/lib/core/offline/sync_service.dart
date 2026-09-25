/// 重连同步服务（范围 A：恢复网络后自动同步队列）—— WP-2M。
///
/// 职责：
/// 1. **重连检测**：监听在线/离线状态流，离线→在线跳变时自动触发排空；
///    也暴露手动 [onReconnect] 供上层（如探活成功）主动调用。
/// 2. **队列排空**：对每条 pending 项调用注入的 [SubmissionGateway] 真正提交；
///    成功后从队列移除 ⇒ 再次排空不会重复提交（本地幂等）；
///    失败时按「可重试 / 终态拒绝」更新重试计数或转 failed。
/// 3. **结果回报**：每次排空产出 [SyncReport]，通过 [onReport] 回调上报上层。
///
/// ⚠️ 本文件**不发起任何网络请求**、**不含任何端点/路径/IP/端口**（契约 §13.10）。
/// 真正发请求的是 [SubmissionGateway] 闭包 —— 由 App 注入，内部走 `ApiClient`
/// 并带上 `Idempotency-Key` 头（复用 `api_client.dart` 既有能力，不改动它）。
library;

import 'dart:async';

import 'submit_queue.dart';

/// 单次提交的结果（由网关产出，离线层据此更新队列）。
sealed class SubmitResult {
  const SubmitResult();
}

/// 服务端已接受（或幂等去重后判定为已接受）。
class SubmitAccepted extends SubmitResult {
  const SubmitAccepted();
}

/// 可重试失败（网络/超时/5xx）：队列保留 pending，等待下次同步。
class SubmitRetryable extends SubmitResult {
  const SubmitRetryable(this.reason);
  final String reason;
}

/// 终态拒绝（如业务 4xx）：队列转 failed，不再自动重试，保留以便上报。
class SubmitRejected extends SubmitResult {
  const SubmitRejected(this.reason);
  final String reason;
}

/// 提交网关：把一条队列项真正发出去。
///
/// 生产环境由 App 注入，典型实现：
/// ```dart
/// SubmissionGateway buildGateway(ApiClient api) => (item) async {
///   try {
///     await api.post('/api/sessions/...', body: item.payload,
///         override: RequestOptionsOverride(headers: {
///           'Idempotency-Key': item.idempotencyKey,  // 契约 §5.1
///         }));
///     return const SubmitAccepted();
///   } on ApiException catch (e) {
///     return (e.kind == ApiErrorKind.network ||
///             e.kind == ApiErrorKind.timeout ||
///             e.kind == ApiErrorKind.server)
///         ? SubmitRetryable(e.message)
///         : SubmitRejected(e.message);
///   }
/// };
/// ```
/// 离线层只认这个类型，端点/路径由闭包捕获，[SyncService] 不关心。
typedef SubmissionGateway = Future<SubmitResult> Function(SubmitQueueItem item);

/// 一次排空的汇报。
class SyncReport {
  const SyncReport({
    required this.succeeded,
    required this.retried,
    required this.rejected,
    required this.remaining,
    this.skipped = false,
  });

  /// 成功提交并从队列移除的条数。
  final int succeeded;

  /// 可重试失败（本回合重试过但仍在队列）的条数。
  final int retried;

  /// 终态拒绝的条数。
  final int rejected;

  /// 排空后仍留在队列（pending + failed）的条数。
  final int remaining;

  /// 是否因「已在排空中」而被跳过（防并发重复提交）。
  final bool skipped;

  @override
  String toString() =>
      'SyncReport(succeeded:$succeeded, retried:$retried, '
      'rejected:$rejected, remaining:$remaining, skipped:$skipped)';
}

/// 重连同步服务。
class SyncService {
  SyncService({required this.queue, required this.gateway, this.onReport});

  final SubmitQueue queue;
  final SubmissionGateway gateway;
  final void Function(SyncReport)? onReport;

  bool _draining = false;
  StreamSubscription<bool>? _watchSub;

  /// 当前正在进行的排空 Future（便于测试等待），无则为 `null`。
  Future<SyncReport>? get lastDrain => _lastDrain;
  Future<SyncReport>? _lastDrain;

  /// 排空队列：对每条 pending 项提交，并更新队列。
  ///
  /// 幂等保证：已 accepted 的项被移除，下一次排空不会再次提交；即便某次提交
  /// 因「ack 丢失」被记为 retryable（项仍在队列），重发时携带**同一**
  /// `idempotencyKey`，由服务端去重，不会重复计分。
  ///
  /// 并发保护：已在排空中再次被调用会直接返回 `skipped`（避免重复提交）。
  Future<SyncReport> drain() async {
    if (_draining) {
      return const SyncReport(
        succeeded: 0,
        retried: 0,
        rejected: 0,
        remaining: 0,
        skipped: true,
      );
    }
    _draining = true;
    final completer = Completer<SyncReport>();
    _lastDrain = completer.future;
    try {
      final pending = await queue.pending();
      var succeeded = 0;
      var retried = 0;
      var rejected = 0;
      for (final item in pending) {
        final result = await gateway(item);
        switch (result) {
          case SubmitAccepted():
            await queue.markSucceeded(item.id);
            succeeded++;
          case SubmitRetryable():
            await queue.markRetryable(item.id, result.reason);
            retried++;
          case SubmitRejected():
            await queue.markFailed(item.id, result.reason);
            rejected++;
        }
      }
      final remaining = (await queue.pending()).length + (await _failedCount());
      final report = SyncReport(
        succeeded: succeeded,
        retried: retried,
        rejected: rejected,
        remaining: remaining,
      );
      onReport?.call(report);
      completer.complete(report);
      return report;
    } on Object catch (e) {
      completer.completeError(e);
      rethrow;
    } finally {
      _draining = false;
    }
  }

  Future<int> _failedCount() async {
    final all = await queue.all();
    return all.where((e) => e.status == SubmitItemStatus.failed).length;
  }

  /// 模拟「重连」：直接触发一次排空（上层探活成功 / 状态流置为在线时调用）。
  Future<SyncReport> onReconnect() => drain();

  /// 订阅在线状态流：离线→在线的跳变自动触发排空。
  ///
  /// [online] 为 `true` 表示当前在线。仅在「上一次为离线/null」且本次为在线时
  /// 才触发，避免重复触发。返回的 subscription 可由 [stop] 取消。
  void watch(Stream<bool> online) {
    var prev = false;
    _watchSub = online.listen((isOnline) async {
      final wasOffline = !prev;
      prev = isOnline;
      if (isOnline && wasOffline) {
        unawaited(drain());
      }
    });
  }

  /// 停止监听（dispose 时调用）。
  Future<void> stop() async {
    await _watchSub?.cancel();
    _watchSub = null;
  }
}
