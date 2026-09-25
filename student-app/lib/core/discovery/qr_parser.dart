/// 服务器地址的**输入解析** —— 30-APP-01 F6/F8。
///
/// 两个来源共用一套解析：
/// - **扫码**：管理端首页二维码内容 `bjsys://192.168.1.80:8810`（接口设计 §3.3）
/// - **手输**：学生照着写 `192.168.1.80`、`192.168.1.80:8810`，
///   甚至可能整条黏贴 `http://192.168.1.80:8810`
///
/// 设计取舍：
/// - **不用 `Uri.parse` 解析自定义 scheme**：`bjsys://` 的默认端口 Dart 会算成 0，
///   且各平台/各扫码库对自定义 scheme 的规范化行为不一致（有的补 `/`、有的转小写）。
///   固定格式的东西用正则更可控、更好报错。
/// - **错误信息必须是能直接上屏的中文**，且要说出「该长什么样」，
///   因为看这行字的是学生，不是运维。
library;

import '../storage/server_store.dart' show ServerAddress, kDefaultServerPort;

/// 二维码内容前缀（接口设计 §3.3）。
const String kQrScheme = 'bjsys';

/// 解析结果。
sealed class ServerLinkResult {
  const ServerLinkResult();
}

/// 解析成功。
class ServerLinkOk extends ServerLinkResult {
  const ServerLinkOk(this.address, {this.fromQr = false});

  final ServerAddress address;
  final bool fromQr;

  @override
  String toString() => 'ServerLinkOk($address)';
}

/// 解析失败，[reason] 为可直接展示的中文原因。
class ServerLinkError extends ServerLinkResult {
  const ServerLinkError(this.reason);

  final String reason;

  @override
  String toString() => 'ServerLinkError($reason)';
}

abstract final class ServerLinkParser {
  /// **扫码只认 `bjsys://`**：规范值只有一个（接口设计 §3.3）。
  /// 若把 `http(s)://` 也放行，学生扫到任何一个网址二维码都会被当成服务器地址，
  /// 然后卡在"连不上"上，还找不到原因。
  static const _qrSchemes = {kQrScheme};

  /// **手输**才容错：有人会把管理端地址整条黏贴进来，那种情况放行更友好。
  static const _manualSchemes = {kQrScheme, 'http', 'https', 'ws', 'wss'};

  static final _withScheme = RegExp(
    r'^([A-Za-z][A-Za-z0-9+.\-]*)://([^/?#:]+)(?::(\d+))?',
  );

  static final _bare = RegExp(r'^([^/?#:]+)(?::(\d+))?$');

  /// 生成二维码内容（管理端渲染、测试 round-trip 共用）。
  static String buildQrContent(ServerAddress address) =>
      '$kQrScheme://${address.authority}';

  /// 解析，失败时给出中文原因。
  static ServerLinkResult parse(String raw, {bool fromQr = false}) {
    // 二维码/黏贴内容常带换行、零宽字符、首尾空格。
    // ⚠️ **只去首尾空格，不删中间的空格** —— 早先图省事用了 `\s+` 全删，
    // 结果手输 "a b c" 被当成合法主机名 "abc"，错误输入静默通过。
    final text = raw
        .replaceAll('\u0000', '')
        .replaceAll('\u200b', '')
        .replaceAll('\r', '')
        .replaceAll('\n', '')
        .trim();

    if (text.isEmpty) {
      return ServerLinkError(fromQr ? '这个二维码里没有内容，换一个试试' : '请先输入服务器地址');
    }

    String host;
    String? portText;
    final allowed = fromQr ? _qrSchemes : _manualSchemes;
    final withScheme = _withScheme.firstMatch(text);
    if (withScheme != null) {
      final scheme = withScheme.group(1)!.toLowerCase();
      if (!allowed.contains(scheme)) {
        return const ServerLinkError('这不是背记系统的二维码，请让老师重新出示');
      }
      host = withScheme.group(2)!;
      portText = withScheme.group(3);
    } else if (text.contains('://')) {
      // 有 "://" 但没匹配上 → scheme 或主机部分不合法
      return ServerLinkError(fromQr ? '这不是背记系统的二维码，请让老师重新出示' : '地址格式不对，例：192.168.1.80');
    } else if (fromQr) {
      // 扫码进来的内容**必须**带 bjsys:// 前缀
      return const ServerLinkError('这不是背记系统的二维码，请让老师重新出示');
    } else {
      final bare = _bare.firstMatch(text);
      if (bare == null) {
        return const ServerLinkError('地址格式不对，例：192.168.1.80 或 192.168.1.80:8810');
      }
      host = bare.group(1)!;
      portText = bare.group(2);
    }

    if (!ServerAddress.isPlausibleHost(host)) {
      return const ServerLinkError('地址格式不对，例：192.168.1.80');
    }

    int port = kDefaultServerPort;
    if (portText != null && portText.isNotEmpty) {
      final parsed = int.tryParse(portText);
      if (!ServerAddress.isValidPort(parsed)) {
        return const ServerLinkError('端口不对，应该在 1 到 65535 之间');
      }
      port = parsed!;
    }

    return ServerLinkOk(ServerAddress(host: host, port: port), fromQr: fromQr);
  }

  /// 只想要地址时的便捷入口（失败返回 `null`）。
  static ServerAddress? tryParse(String raw, {bool fromQr = false}) {
    final result = parse(raw, fromQr: fromQr);
    return result is ServerLinkOk ? result.address : null;
  }
}
