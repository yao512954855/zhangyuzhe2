/// UDP 广播发现的**原生实现**（Android / iOS / 桌面）。
///
/// 只在 `dart.library.io` 存在时参与编译（见 `broadcast_finder_factory.dart`）。
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../storage/server_store.dart';
import 'broadcast_finder.dart';

/// 创建平台实现。
BroadcastFinder createBroadcastFinder() => IoBroadcastFinder();

/// 基于 [RawDatagramSocket] 的广播发现。
class IoBroadcastFinder implements BroadcastFinder {
  RawDatagramSocket? _socket;
  String? _lastError;

  @override
  bool get supported => true;

  @override
  String? get lastError => _lastError;

  @override
  Future<ServerAddress?> find({
    Duration timeout = kDiscoverTimeout,
    int retries = kDiscoverRetries,
    void Function(String status)? onStatus,
  }) async {
    _lastError = null;
    for (var attempt = 0; attempt <= retries; attempt++) {
      final round = attempt + 1;
      onStatus?.call('正在寻找服务器…（第 $round 次）');
      final found = await _once(timeout);
      if (found != null) {
        _lastError = null;
        return found;
      }
    }
    _lastError = '同一个网络里没有找到服务器，可以让老师给你扫二维码';
    return null;
  }

  /// 单轮广播：发一次探测包，等 [timeout]，收到第一个合法应答就返回。
  Future<ServerAddress?> _once(Duration timeout) async {
    RawDatagramSocket socket;
    try {
      // 端口 0 = 让系统分配，避免与系统服务抢端口
      socket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
    } on SocketException catch (e) {
      _lastError = '无法使用网络查找：${e.message}';
      return null;
    }

    _socket = socket;
    final completer = Completer<ServerAddress?>();
    late StreamSubscription<RawSocketEvent> sub;
    Timer? timer;

    void finish(ServerAddress? value) {
      if (!completer.isCompleted) {
        completer.complete(value);
      }
    }

    try {
      socket.broadcastEnabled = true;

      // 必须先注册监听再 send：Dart 的 RawDatagramSocket 只在监听到 read 事件
      // 后才能 receive()，反过来的话应答包可能已经到达却被丢弃。
      sub = socket.listen((event) {
        if (event != RawSocketEvent.read) {
          return;
        }
        final datagram = socket.receive();
        if (datagram == null) {
          return;
        }
        Object? decoded;
        try {
          decoded = jsonDecode(utf8.decode(datagram.data, allowMalformed: true));
        } catch (_) {
          return; // 不是 JSON 的包不是我们关心的
        }
        final address = parseDiscoverReply(
          decoded,
          replyFrom: datagram.address.address,
        );
        if (address != null) {
          finish(address);
        }
      });

      timer = Timer(timeout, () => finish(null));

      final payload = utf8.encode(
        jsonEncode({'magic': kDiscoverMagic, 'v': 1}),
      );
      socket.send(payload, InternetAddress('255.255.255.255'), kDiscoveryPort);

      return await completer.future;
    } finally {
      timer?.cancel();
      await sub.cancel();
      socket.close();
      _socket = null;
    }
  }

  @override
  Future<void> dispose() async {
    _socket?.close();
    _socket = null;
  }
}
