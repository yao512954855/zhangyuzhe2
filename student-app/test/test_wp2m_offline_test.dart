/// WP-2M 离线缓存（范围 A）—— 单元验收。
///
/// 覆盖验收点：
/// U1 断网 → 读缓存命中（已缓存的待练内容离线可读）
/// U2 断网 → 读缓存未命中（返回 null，UI 区分「无缓存」）
/// U3 断网 → 提交进队列（练习结果进本地提交队列，带幂等键）
/// U4 重连 → 队列排空（成功项被移除）
/// U5 重连 → 幂等（重复同步不重复提交；丢失 ack 重发仍只计一次）
/// U6 队列持久化（重启后仍在 —— 内存后端 + SharedPreferences 后端各验）
/// U7 失败重试计数（可重试失败每次 +1）
/// U8 【反向验证】撤掉幂等键 ⇒ 丢失 ack 重发会被服务端计两次（U5 会转红）
///
/// ⚠️ 不依赖真网络（flutter test 默认拦截真请求，见 02-实现约定 §九）。
/// 网关用 [FakeSubmitServer] 模拟服务端按 `Idempotency-Key` 去重。

library;

import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/offline/cache_store.dart';
import 'package:student_app/core/offline/kv_store.dart';
import 'package:student_app/core/offline/submit_queue.dart';
import 'package:student_app/core/offline/sync_service.dart';

// ---------------------------------------------------------------------------
// 假服务端：按幂等键去重，统计「真正落库次数」。
// - 非空 key 第一次见到 → 落库 +1；第二次见到 → 去重（不再 +1）。
// - 空 key（= 客户端没带幂等键）→ 无法去重，每次都当作新提交（+1）。
// - lostAck=true 时：即便已落库，也向客户端回「可重试」（模拟 ack 丢失），
//   客户端拿不到成功确认 ⇒ 项仍留队列 ⇒ 会有一次重发。
// ---------------------------------------------------------------------------
class FakeSubmitServer {
  FakeSubmitServer({this.lostAck = false});

  final bool lostAck;
  final Set<String> _appliedKeys = {};
  final List<String> receivedKeys = [];

  /// 真正落库的次数（去重后）。
  int appliedCount = 0;

  /// 被去重的重复提交次数。
  int duplicateAttempts = 0;

  SubmissionGateway get gateway => (SubmitQueueItem item) async {
        receivedKeys.add(item.idempotencyKey);
        final key = item.idempotencyKey;
        if (key.isEmpty) {
          // 没有幂等键：服务端无从去重，每次都是新提交。
          appliedCount++;
          return lostAck
              ? const SubmitRetryable('simulated lost ack')
              : const SubmitAccepted();
        }
        if (_appliedKeys.contains(key)) {
          duplicateAttempts++;
          // 已落库：去重返回成功，但绝不再 +1。
          return const SubmitAccepted();
        }
        _appliedKeys.add(key);
        appliedCount++;
        return lostAck
            ? const SubmitRetryable('simulated lost ack')
            : const SubmitAccepted();
      };
}

/// 等 watch() 触发并跑完一次排空。
Future<SyncReport> _awaitWatchDrain(SyncService s) async {
  for (var i = 0; i < 500 && s.lastDrain == null; i++) {
    await Future<void>.delayed(Duration.zero);
  }
  return await s.lastDrain!;
}

void main() {
  // =========================================================================
  // U1 / U2：内容缓存（断网可读）
  // =========================================================================
  group('内容缓存 CacheStore', () {
    late MemoryKvStore store;
    late CacheStore cache;

    setUp(() {
      store = MemoryKvStore();
      cache = CacheStore(store: store, namespace: 'practice');
    });

    test('U1 断网 → 读缓存命中：在线时写入的待练内容离线可读', () async {
      const content = {
        'unit_id': 11,
        'unit_name': 'Unit 1',
        'items': [
          {'word_id': 88, 'en': 'classroom', 'cn': '教室'},
          {'word_id': 89, 'en': 'teacher', 'cn': '老师'},
        ],
      };
      // 在线时写缓存（模拟「拿到组卷结果后顺手缓存」）。
      await cache.put('unit:11', content);

      // 断网后（无网络也）直接读缓存。
      final got = cache.get('unit:11');
      expect(got, isNotNull);
      expect(got, equals(content));
      expect(cache.contains('unit:11'), isTrue);
    });

    test('U2 断网 → 读缓存未命中：未知条目返回 null（UI 据此提示无离线内容）', () {
      expect(cache.get('unit:999'), isNull);
      expect(cache.contains('unit:999'), isFalse);
    });

    test('缓存损坏（非法 JSON）静默降级为 null，不抛', () async {
      await store.writeString('practice.corrupt', '{这不是合法json');
      expect(cache.get('corrupt'), isNull);
    });

    test('命名空间隔离：clear 只清本空间', () async {
      await cache.put('a', {'x': 1});
      await store.writeString('other.b', 'v');
      await cache.clear();
      expect(cache.contains('a'), isFalse);
      expect(store.readString('other.b'), 'v');
    });
  });

  // =========================================================================
  // U3 / U4 / U5 / U6 / U7 / U8：提交队列 + 同步
  // =========================================================================
  group('提交队列 SubmitQueue', () {
    late MemoryKvStore store;

    setUp(() {
      store = MemoryKvStore();
    });

    test('U3 断网 → 提交进队列：离线时结果进队列且带幂等键', () async {
      final queue = SubmitQueue(store: store, storeKey: 'q');
      final item = await queue.enqueue(
        payload: {'session_id': 1, 'word_id': 88, 'asr_text': 'classroom'},
        idempotencyKey: 'sid1-w88-1',
      );
      final pending = await queue.pending();
      expect(pending, hasLength(1));
      expect(item.idempotencyKey, 'sid1-w88-1');
      expect(pending.single.idempotencyKey, 'sid1-w88-1');
      expect(pending.single.attempts, 0);
    });

    test('U3 幂等入队：同幂等键不重复入队', () async {
      final queue = SubmitQueue(store: store, storeKey: 'q');
      final a = await queue.enqueue(
        payload: {'x': 1},
        idempotencyKey: 'dup-key',
      );
      final b = await queue.enqueue(
        payload: {'x': 2},
        idempotencyKey: 'dup-key',
      );
      expect(a.id, b.id); // 命中去重，返回同一条
      expect(await queue.pending(), hasLength(1));
    });

    test('U6 队列持久化（内存后端）：重启后仍在', () async {
      final q1 = SubmitQueue(store: store, storeKey: 'q');
      await q1.enqueue(payload: {'a': 1}, idempotencyKey: 'k1');
      await q1.enqueue(payload: {'b': 2}, idempotencyKey: 'k2');

      // 模拟「进程重启」：用同一个 store 重新构造队列。
      final q2 = SubmitQueue(store: store, storeKey: 'q');
      expect(await q2.pending(), hasLength(2));
    });

    test('U6 队列持久化（SharedPreferences 后端）：重启后仍在', () async {
      SharedPreferences.setMockInitialValues({});
      final sp = await SharedPreferences.getInstance();
      final q1 = SubmitQueue(store: SharedPreferencesKvStore(sp), storeKey: 'offline.q');
      await q1.enqueue(payload: {'a': 1}, idempotencyKey: 'k1');
      await q1.enqueue(payload: {'b': 2}, idempotencyKey: 'k2');

      // 全新实例（同一 SharedPreferences 单例 = 同一落盘）。
      final q2 = SubmitQueue(store: SharedPreferencesKvStore(sp), storeKey: 'offline.q');
      expect(await q2.pending(), hasLength(2));
    });

    test('U7 失败重试计数：可重试失败时每次 +1 且仍留队', () async {
      final queue = SubmitQueue(store: store, storeKey: 'q');
      await queue.enqueue(payload: {'a': 1}, idempotencyKey: 'k1');

      // 让网关永远返回「可重试失败」。
      SubmissionGateway alwaysRetry = (_) async => const SubmitRetryable('down');
      final sync = SyncService(queue: queue, gateway: alwaysRetry);

      await sync.drain();
      await sync.drain();
      await sync.drain();

      final pending = await queue.pending();
      expect(pending, hasLength(1));
      expect(pending.single.attempts, 3); // 三次排空各 +1
      expect(pending.single.lastError, 'down');
    });

    test('U7 maxAttempts 达上限则转终态 failed，不再自动重试', () async {
      final queue = SubmitQueue(store: store, storeKey: 'q', maxAttempts: 2);
      await queue.enqueue(payload: {'a': 1}, idempotencyKey: 'k1');
      final sync = SyncService(queue: queue, gateway: (_) async => const SubmitRetryable('down'));

      await sync.drain(); // attempts=1, 仍 pending
      await sync.drain(); // attempts=2 == max ⇒ failed
      final all = await queue.all();
      expect(all, hasLength(1));
      expect(all.single.status, SubmitItemStatus.failed);
      expect(all.single.attempts, 2);
    });
  });

  group('同步 SyncService（重连排空 + 幂等）', () {
    late MemoryKvStore store;

    setUp(() {
      store = MemoryKvStore();
    });

    test('U4 重连 → 队列排空：成功项被移除', () async {
      final queue = SubmitQueue(store: store, storeKey: 'q');
      final server = FakeSubmitServer();
      await queue.enqueue(payload: {'a': 1}, idempotencyKey: 'k1');
      await queue.enqueue(payload: {'b': 2}, idempotencyKey: 'k2');
      await queue.enqueue(payload: {'c': 3}, idempotencyKey: 'k3');

      final sync = SyncService(queue: queue, gateway: server.gateway);
      final report = await sync.onReconnect();

      expect(report.succeeded, 3);
      expect(report.remaining, 0);
      expect(await queue.pending(), isEmpty);
      expect(server.appliedCount, 3);
    });

    test('U5 重连 → 幂等：排空两次，每条只提交一次（不重复提交）', () async {
      final queue = SubmitQueue(store: store, storeKey: 'q');
      final server = FakeSubmitServer();
      await queue.enqueue(payload: {'a': 1}, idempotencyKey: 'k1');
      await queue.enqueue(payload: {'b': 2}, idempotencyKey: 'k2');

      final sync = SyncService(queue: queue, gateway: server.gateway);
      await sync.onReconnect(); // 第一次排空
      await sync.onReconnect(); // 第二次（重连/重复同步）

      // 关键断言：服务端只真正落库 2 次，且没有重复提交。
      expect(server.appliedCount, 2);
      expect(server.duplicateAttempts, 0);
      // 收到的 key 集合与去重后的 key 集合一致（无重复发送）。
      expect(server.receivedKeys.toSet().length, server.receivedKeys.length);
      expect(await queue.pending(), isEmpty);
    });

    test('U5 幂等键稳定：ack 丢失导致重发，服务端仍只计一次', () async {
      final queue = SubmitQueue(store: store, storeKey: 'q');
      final server = FakeSubmitServer(lostAck: true);
      await queue.enqueue(payload: {'a': 1}, idempotencyKey: 'k-stable');

      final sync = SyncService(queue: queue, gateway: server.gateway);
      await sync.onReconnect(); // 第一次：服务端落库，但回 retryable（ack 丢失）
      expect((await queue.pending()).single.attempts, 1); // 仍留队
      await sync.onReconnect(); // 第二次：携带同一 key 重发 → 服务端去重

      // ⚠️ 反向验证锚点：本用例断言「只计一次」。
      // 若把 enqueue 的 idempotencyKey 置空（或在网关里丢弃 Idempotency-Key 头），
      // appliedCount 会变成 2，本断言失败 ⇒ 用例转红。
      expect(server.appliedCount, 1);
      expect(server.duplicateAttempts, 1);
      expect(await queue.pending(), isEmpty);
    });

    test('U8【反向验证】漏带 Idempotency-Key 头 ⇒ ack 丢失重发会被服务端计两次', () async {
      final queue = SubmitQueue(store: store, storeKey: 'q');
      final server = FakeSubmitServer(lostAck: true);
      // 队列里存的是「稳定幂等键」—— 正常实现本应带上它。
      await queue.enqueue(payload: {'a': 1}, idempotencyKey: 'k-stable-2');

      // 退化网关：发请求时**漏带 Idempotency-Key 头**（模拟契约 §5.1 被破坏）。
      // 此时服务端收不到幂等键，即便 key 在本地是稳定的也无处去重。
      SubmissionGateway brokenGateway = (SubmitQueueItem item) async {
        final noKey = SubmitQueueItem(
          id: item.id,
          idempotencyKey: '', // 漏带 Idempotency-Key 头
          payload: item.payload,
          createdAt: item.createdAt,
          attempts: item.attempts,
          status: item.status,
          lastError: item.lastError,
          lastAttemptAt: item.lastAttemptAt,
        );
        return server.gateway(noKey);
      };

      final sync = SyncService(queue: queue, gateway: brokenGateway);
      await sync.onReconnect(); // 落库 1 次，回 retryable（ack 丢失）
      await sync.onReconnect(); // 重发：服务端仍收不到 key → 当作新提交，再落库 1 次

      // 正是「漏带 Idempotency-Key 头」的危险结果：同一逻辑提交被计了两次。
      // 反向验证：只要把上面 brokenGateway 改回正常（带上 key），appliedCount 即变 1。
      expect(server.appliedCount, 2);
    });

    test('重连检测：离线→在线跳变自动触发排空', () async {
      final queue = SubmitQueue(store: store, storeKey: 'q');
      final server = FakeSubmitServer();
      await queue.enqueue(payload: {'a': 1}, idempotencyKey: 'k1');

      final sync = SyncService(queue: queue, gateway: server.gateway);
      final controller = StreamController<bool>.broadcast();
      sync.watch(controller.stream);

      controller.add(false); // 离线
      controller.add(true); // 在线（跳变触发）
      final report = await _awaitWatchDrain(sync);

      expect(report.succeeded, 1);
      expect(server.appliedCount, 1);
      expect(await queue.pending(), isEmpty);
      await sync.stop();
      await controller.close();
    });

    test('并发保护：排空中再次 drain 返回 skipped，不会重复提交', () async {
      final queue = SubmitQueue(store: store, storeKey: 'q');
      final server = FakeSubmitServer();
      // 用阻塞网关制造「排空中」窗口。
      final gate = Completer<void>();
      SubmissionGateway blocking = (item) async {
        await gate.future;
        return server.gateway(item); // 真正提交，才会计入 appliedCount
      };
      await queue.enqueue(payload: {'a': 1}, idempotencyKey: 'k1');
      final sync = SyncService(queue: queue, gateway: blocking);

      final first = sync.drain(); // 进入排空（被 gate 卡住）
      // 在排空中再次触发（重连重复到达）。
      final second = sync.onReconnect();
      gate.complete(); // 放行第一次
      final r1 = await first;
      final r2 = await second;

      expect(r1.succeeded, 1);
      expect(r2.skipped, isTrue);
      expect(server.appliedCount, 1); // 只提交一次
    });
  });
}
