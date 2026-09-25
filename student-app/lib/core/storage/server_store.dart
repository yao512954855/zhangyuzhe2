/// 服务器地址与「最近成功地址」记忆 —— 30-APP-01 F9。
///
/// 服务器 IP 会变（DHCP）。策略是**永不让学生去改 IP**：
/// ① 启动优先试上次成功的地址 → ② 失败静默走发现流程（广播/扫码）。
/// 所以这里既要有 `current`，也要有一小串 `recents` 兜底
/// （办公室/教室两个网段来回切的时候，两次都能命中缓存）。
library;

import 'package:flutter/foundation.dart';

import 'local_store.dart';

/// 契约 §11 与接口设计 §3 约定的默认端口。
const int kDefaultServerPort = 8810;

/// 一个服务器地址。
@immutable
class ServerAddress {
  const ServerAddress({required this.host, required this.port});

  final String host;
  final int port;

  /// 与部署契约一致：局域网走 `http`（原生 App 不受浏览器混合内容限制）。
  Uri get baseUri => Uri(scheme: 'http', host: host, port: port);

  /// WebSocket 地址：同 host/port，路径 `/ws/asr`。
  Uri wsUri(String path, [Map<String, String>? query]) =>
      Uri(scheme: 'ws', host: host, port: port, path: path, queryParameters: query);

  /// `192.168.1.80:8810`
  String get authority => '$host:$port';

  /// 给人看的（设置页/调试），学生界面不出现术语。
  String get display => authority;

  /// 从 `host:port` / `host` 反序列化（`recents` 用这个格式落库）。
  static ServerAddress? fromAuthority(String raw) {
    final text = raw.trim();
    if (text.isEmpty) {
      return null;
    }
    final idx = text.lastIndexOf(':');
    if (idx < 0) {
      return isPlausibleHost(text)
          ? ServerAddress(host: text, port: kDefaultServerPort)
          : null;
    }
    final host = text.substring(0, idx);
    final port = int.tryParse(text.substring(idx + 1));
    if (!isPlausibleHost(host) || !isValidPort(port)) {
      return null;
    }
    return ServerAddress(host: host, port: port!);
  }

  /// 端口合法范围（1–65535）。
  static bool isValidPort(int? port) => port != null && port >= 1 && port <= 65535;

  /// 主机名是否像样：IPv4（四段 0–255）或常见主机名。
  ///
  /// **故意不校验私网段**：教室网段五花八门（192.168 / 10 / 172.16 都可能），
  /// 客户端拦一刀只会把能用的地址挡在外面；服务端已经拒绝公网来源（契约 §3.2）。
  static bool isPlausibleHost(String host) {
    final h = host.trim();
    if (h.isEmpty || h.length > 253 || h.contains(' ')) {
      return false;
    }
    // IPv4？
    final parts = h.split('.');
    if (parts.length == 4 && parts.every((p) => int.tryParse(p) != null)) {
      return parts.every((p) {
        final n = int.parse(p);
        return n >= 0 && n <= 255 && (p == '0' || !p.startsWith('0'));
      });
    }
    // 主机名：字母/数字/连字符/点，不以连字符开头结尾
    return RegExp(r'^[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?$').hasMatch(h);
  }

  @override
  bool operator ==(Object other) =>
      other is ServerAddress && other.host == host && other.port == port;

  @override
  int get hashCode => Object.hash(host, port);

  @override
  String toString() => authority;
}

/// 服务器地址的读写与记忆。
class ServerStore extends ChangeNotifier {
  ServerStore(this._local) {
    final host = _local.serverHost;
    final port = _local.serverPort;
    if (host != null && port != null && ServerAddress.isPlausibleHost(host)) {
      _current = ServerAddress(host: host, port: port);
    }
  }

  final LocalStore _local;

  /// 最多记 5 个（够覆盖"家里/教室/办公室"，再多也没意义）。
  static const int maxRecents = 5;

  ServerAddress? _current;

  ServerAddress? get current => _current;

  bool get hasServer => _current != null;

  /// 最近成功连接的地址，最新在前。
  List<ServerAddress> get recents {
    return _local.serverRecents
        .map(ServerAddress.fromAuthority)
        .whereType<ServerAddress>()
        .toList(growable: false);
  }

  /// 保存并记住（写盘后 [notifyListeners]，调用方 await 完即可发请求）。
  Future<void> save(ServerAddress address) async {
    _current = address;
    final merged = <ServerAddress>[
      address,
      ...recents.where((a) => a != address),
    ];
    await _local.saveServer(
      host: address.host,
      port: address.port,
      recents: merged
          .take(maxRecents)
          .map((a) => a.authority)
          .toList(growable: false),
    );
    notifyListeners();
  }

  /// 请求失败且确认是"连不上"时调 —— 只清当前地址，**保留 recents**，
  /// 否则下一次启动连可试的候选都没有了。
  Future<void> forget() async {
    _current = null;
    await _local.clearServer();
    notifyListeners();
  }

  /// 换服务器（重新扫码）时用：当前 + 历史全清。
  Future<void> reset() async {
    _current = null;
    await _local.saveServer(
      host: '',
      port: kDefaultServerPort,
      recents: const [],
    );
    await _local.clearServer();
    notifyListeners();
  }
}
