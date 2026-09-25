/// 提交队列（范围 A：练习结果进本地提交队列）—— WP-2M。
///
/// 设计要点（对验收逐条负责）：
/// 1. **持久化**：每次变更都即时写 [KvStore]，进程重启后用同一个 store
///    重新构造队列即可恢复 —— 不依赖内存。
/// 2. **幂等键**：每条队列项带一个稳定的 `idempotencyKey`，提交时由上层网关
///    作为 `Idempotency-Key` 头发给服务端（契约 §5.1）。键稳定 ⇒ 即使因为
///    「服务端已落库但客户端没收到 ack」而重发，服务端也能去重，不会重复计分。
/// 3. **失败重试计数**：每次提交失败（可重试）`attempts++`；累计次数暴露给上层
///    做退避与展示，绝不静默吞掉。
/// 4. **去重入队**：若传入了非空的 `idempotencyKey` 且已有同键的 pending 项，
///    直接忽略本次入队（同一个逻辑提交不会被排两次）。
///
/// 本类**不发起任何网络请求**、**不含任何端点/路径/IP/端口**（契约 §13.10）。
/// 真正发请求的是 [SyncService] 注入的网关闭包。
library;

import 'dart:convert';

import 'kv_store.dart';

/// 单条待提交项。
///
/// 设计上不可变：状态变化一律通过 [SubmitQueueItem.copyWith] 产生新实例，
/// 便于测试断言与避免共享可变状态。
class SubmitQueueItem {
  const SubmitQueueItem({
    required this.id,
    required this.idempotencyKey,
    required this.payload,
    required this.createdAt,
    this.attempts = 0,
    this.status = SubmitItemStatus.pending,
    this.lastError,
    this.lastAttemptAt,
  });

  /// 本地唯一 id（仅用于本地索引，不参与幂等）。
  final String id;

  /// 幂等键：发往服务端时作为 `Idempotency-Key` 头。必须稳定且非空才能去重。
  final String idempotencyKey;

  /// 练习结果提交体（业务 JSON，离线层不解释其字段）。
  final Map<String, dynamic> payload;

  /// 入队时间，ISO 8601 UTC。
  final String createdAt;

  /// 失败重试计数（仅「可重试」失败累加）。
  final int attempts;

  /// 状态：pending（待提交）/ failed（终态，不再自动重试）。
  final SubmitItemStatus status;

  /// 最近一次失败原因（仅记录，不展示技术细节）。
  final String? lastError;

  /// 最近一次尝试时间，ISO 8601 UTC。
  final String? lastAttemptAt;

  SubmitQueueItem copyWith({
    int? attempts,
    SubmitItemStatus? status,
    String? lastError,
    String? lastAttemptAt,
  }) =>
      SubmitQueueItem(
        id: id,
        idempotencyKey: idempotencyKey,
        payload: payload,
        createdAt: createdAt,
        attempts: attempts ?? this.attempts,
        status: status ?? this.status,
        lastError: lastError ?? this.lastError,
        lastAttemptAt: lastAttemptAt ?? this.lastAttemptAt,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'idempotency_key': idempotencyKey,
        'payload': payload,
        'created_at': createdAt,
        'attempts': attempts,
        'status': status == SubmitItemStatus.pending ? 'pending' : 'failed',
        if (lastError != null) 'last_error': lastError,
        if (lastAttemptAt != null) 'last_attempt_at': lastAttemptAt,
      };

  factory SubmitQueueItem.fromJson(Map<String, dynamic> json) => SubmitQueueItem(
        id: json['id'] as String,
        idempotencyKey: json['idempotency_key'] as String,
        payload: Map<String, dynamic>.from(json['payload'] as Map),
        createdAt: json['created_at'] as String,
        attempts: json['attempts'] as int? ?? 0,
        status: switch (json['status']) {
          'failed' => SubmitItemStatus.failed,
          _ => SubmitItemStatus.pending,
        },
        lastError: json['last_error'] as String?,
        lastAttemptAt: json['last_attempt_at'] as String?,
      );
}

/// 队列项状态。
enum SubmitItemStatus {
  /// 待提交（含「提交过但可重试失败」）。
  pending,

  /// 终态：被服务端明确拒绝（如业务 4xx），不再自动重试，保留以便上报/排查。
  failed,
}

/// 本地提交队列。
class SubmitQueue {
  SubmitQueue({required this._store, required this._storeKey, this.maxAttempts = 0});

  final KvStore _store;
  final String _storeKey;

  /// 单个 pending 项的最大可重试次数；`0` = 不限（每次重连都再试，永不丢数据）。
  final int maxAttempts;

  List<SubmitQueueItem> _items = const [];
  bool _loaded = false;
  int _seq = 0;

  /// 从持久化后端懒加载（进程重启后第一次访问时恢复）。
  Future<void> _ensureLoaded() async {
    if (_loaded) {
      return;
    }
    final raw = _store.readString(_storeKey);
    if (raw != null) {
      try {
        final decoded = jsonDecode(raw);
        if (decoded is List) {
          _items = [
            for (final e in decoded)
              if (e is Map<String, dynamic>) SubmitQueueItem.fromJson(e),
          ];
          // 用已恢复条目数校准序列，降低本地 id 碰撞概率。
          _seq = _items.length;
        }
      } on Object {
        // 队列文件损坏：宁可丢队列（断网期间没攒下的提交），也不能崩。
        _items = const [];
      }
    }
    _loaded = true;
  }

  Future<void> _persist() =>
      _store.writeString(_storeKey, jsonEncode(_items.map((e) => e.toJson()).toList()));

  String _genId() {
    _seq += 1;
    return '${DateTime.now().microsecondsSinceEpoch.toRadixString(36)}_$_seq';
  }

  /// 入队一条练习结果。
  ///
  /// [idempotencyKey] 为空时自动生成（每条独立）；传入非空且已有同键 **pending**
  /// 项时，直接忽略本次入队（幂等入队，同一逻辑提交不会被排两次）。
  /// 返回实际入队的项（去重命中时返回既存项）。
  Future<SubmitQueueItem> enqueue({
    required Map<String, dynamic> payload,
    String? idempotencyKey,
  }) async {
    await _ensureLoaded();
    final key = idempotencyKey?.isNotEmpty == true
        ? idempotencyKey!
        : _genId();

    final existing = _items.where((e) => e.status == SubmitItemStatus.pending).cast<SubmitQueueItem?>().firstWhere(
          (e) => e?.idempotencyKey == key,
          orElse: () => null,
        );
    if (existing != null) {
      return existing; // 去重：不重复入队
    }

    final item = SubmitQueueItem(
      id: _genId(),
      idempotencyKey: key,
      payload: payload,
      createdAt: DateTime.now().toUtc().toIso8601String(),
    );
    _items = [..._items, item];
    await _persist();
    return item;
  }

  /// 当前所有项（含 failed 终态）。
  Future<List<SubmitQueueItem>> all() async {
    await _ensureLoaded();
    return List<SubmitQueueItem>.from(_items, growable: false);
  }

  /// 当前待提交项（不含 failed 终态）。
  Future<List<SubmitQueueItem>> pending() async {
    await _ensureLoaded();
    return _items
        .where((e) => e.status == SubmitItemStatus.pending)
        .toList(growable: false);
  }

  /// 待提交项数量（便捷只读，已加载后可用）。
  Future<int> get pendingCount async => (await pending()).length;

  /// 标记提交成功：从队列移除（**这是「重复同步不重复提交」的本地保证**）。
  Future<void> markSucceeded(String localId) async {
    await _ensureLoaded();
    final before = _items.length;
    _items = _items.where((e) => e.id != localId).toList(growable: false);
    if (_items.length != before) {
      await _persist();
    }
  }

  /// 标记「可重试失败」：累加重试计数并保留 pending，等待下次同步重试。
  ///
  /// 若设置了 [maxAttempts] 且已达上限，则降级为终态 [SubmitItemStatus.failed]，
  /// 避免无限重试阻塞队列；默认 `maxAttempts=0` 时永不自动放弃。
  Future<void> markRetryable(String localId, String reason) async {
    await _ensureLoaded();
    final idx = _items.indexWhere((e) => e.id == localId);
    if (idx < 0) {
      return;
    }
    final cur = _items[idx];
    final nextAttempts = cur.attempts + 1;
    final nextStatus = (maxAttempts > 0 && nextAttempts >= maxAttempts)
        ? SubmitItemStatus.failed
        : SubmitItemStatus.pending;
    _items = [
      for (var i = 0; i < _items.length; i++)
        if (i == idx)
          cur.copyWith(
            attempts: nextAttempts,
            status: nextStatus,
            lastError: reason,
            lastAttemptAt: DateTime.now().toUtc().toIso8601String(),
          )
        else
          _items[i],
    ];
    await _persist();
  }

  /// 标记「终态失败」：服务端明确拒绝，保留以便上报，不再自动重试。
  Future<void> markFailed(String localId, String reason) async {
    await _ensureLoaded();
    final idx = _items.indexWhere((e) => e.id == localId);
    if (idx < 0) {
      return;
    }
    final cur = _items[idx];
    _items = [
      for (var i = 0; i < _items.length; i++)
        if (i == idx)
          cur.copyWith(
            status: SubmitItemStatus.failed,
            lastError: reason,
            lastAttemptAt: DateTime.now().toUtc().toIso8601String(),
          )
        else
          _items[i],
    ];
    await _persist();
  }

  /// 丢弃全部队列项（如：用户主动清空离线提交）。
  Future<void> clear() async {
    _items = const [];
    await _persist();
  }
}
