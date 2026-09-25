/// UDP 广播发现 —— 30-APP-01 F7（接口设计 §3.2）。
///
/// 本文件是**纯 Dart 接口层**，不含 `dart:io`，因此 Web 目标也能编译。
/// 真实现见 `broadcast_finder_io.dart`，Web 空实现见 `broadcast_finder_web.dart`，
/// 由 `broadcast_finder_factory.dart` 按平台条件导入。
///
/// ⚠️ 广播只是**增强**：部分安卓版本/路由器开了 AP 隔离，广播包出不去。
/// 所以扫码才是主路径（UI 规范 P1 也把扫码做成最大的主按钮），
/// 广播失败必须**静默降级**，绝不能弹错误吓学生。
library;

import '../storage/server_store.dart';

/// 发现端口（接口设计 §3.2 约定，与 HTTP 端口无关）。
const int kDiscoveryPort = 9527;

/// 探测包 magic。
const String kDiscoverMagic = 'BJSYS_DISCOVER';

/// 应答包 magic。
const String kHereMagic = 'BJSYS_HERE';

/// 单轮等待时长（契约：等待 2 秒）。
const Duration kDiscoverTimeout = Duration(seconds: 2);

/// 重试次数（契约：可重试 2 次，即最多 3 轮）。
const int kDiscoverRetries = 2;

/// 广播发现的能力接口。
abstract class BroadcastFinder {
  /// 本平台是否支持广播发现（Web 为 `false`，UI 据此跳过这一步）。
  bool get supported;

  /// 最近一次失败原因（中文，可上屏；成功时为 `null`）。
  String? get lastError;

  /// 尝试发现服务器；找不到返回 `null`（**不抛异常**）。
  ///
  /// [onStatus] 用于把「正在找…（第 1/3 次）」这类过程反馈给 UI。
  Future<ServerAddress?> find({
    Duration timeout = kDiscoverTimeout,
    int retries = kDiscoverRetries,
    void Function(String status)? onStatus,
  });

  /// 释放资源（幂等）。
  Future<void> dispose();
}

/// 解析应答包 → 地址。
///
/// [replyFrom] 是 UDP 包的**源 IP**：当应答里的 `host` 不可信
/// （空 / `0.0.0.0` / 回环）时用它兜底 —— 服务端多网卡时可能报错网卡，
/// 而「包从哪来」永远是对的。
///
/// ⚠️ [fallbackPort] 默认是 **HTTP 服务端口 8810**，不是 UDP 发现端口 9527。
/// 这两个都是"默认端口"但含义完全不同，写错的话客户端会去连 9527 ——
/// 那是只有 UDP 在听的端口，表现为"永远连不上"。
ServerAddress? parseDiscoverReply(
  Object? decoded, {
  String? replyFrom,
  int fallbackPort = kDefaultServerPort,
}) {
  if (decoded is! Map) {
    return null;
  }
  if (decoded['magic'] != kHereMagic) {
    return null;
  }
  final rawHost = decoded['host']?.toString().trim() ?? '';
  var host = rawHost;
  if (host.isEmpty || host == '0.0.0.0' || host == '127.0.0.1' || host == '::') {
    host = (replyFrom ?? '').trim();
  }
  if (!ServerAddress.isPlausibleHost(host)) {
    return null;
  }
  final port = int.tryParse(decoded['port']?.toString() ?? '') ?? fallbackPort;
  if (!ServerAddress.isValidPort(port)) {
    return null;
  }
  return ServerAddress(host: host, port: port);
}
