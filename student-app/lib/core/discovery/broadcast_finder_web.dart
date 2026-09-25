/// UDP 广播发现的 **Web 空实现**。
///
/// 浏览器沙箱里没有裸 UDP socket，这条路径在 Web 上不存在。
/// 保留它有两个作用：
/// ① 让 `flutter run -d chrome`（开发期验 UI 用）能编译通过；
/// ② 明确告诉上层「本平台不支持」，UI 好直接引导到扫码，而不是转 2 秒空圈。
///
/// ⚠️ **生产目标始终是安卓真机**（30-APP-01 关键要点：原生优势，不退回 WebView）。
library;

import '../storage/server_store.dart';
import 'broadcast_finder.dart';

/// 创建平台实现。
BroadcastFinder createBroadcastFinder() => WebBroadcastFinder();

/// 什么都不做的实现。
class WebBroadcastFinder implements BroadcastFinder {
  @override
  bool get supported => false;

  @override
  String? get lastError => '浏览器里不能自动查找，请扫码或手动输入地址';

  @override
  Future<ServerAddress?> find({
    Duration timeout = kDiscoverTimeout,
    int retries = kDiscoverRetries,
    void Function(String status)? onStatus,
  }) async {
    onStatus?.call('当前环境不支持自动查找');
    return null;
  }

  @override
  Future<void> dispose() async {}
}
