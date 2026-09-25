/// `/ws/asr` 客户端封装 —— 30-APP-01 F11。
///
/// 与后端 `backend/app/api/asr_ws.py`（WP-BE-06）严格对齐：
/// - **控制帧走文本**：`start` / `end` / `cancel` / `ping`（JSON）
/// - **音频帧走二进制**：裸 PCM，**不要 base64**（后端 `handle_bytes` 直接吃 bytes）
///
/// 关闭码语义（后端约定，必须区分对待，否则会出现"token 过期了还傻傻重连"）：
/// - `4001` 认证失败 → **不重连**，回调上层跳登录
/// - `4002` 同账号被新连接踢掉 → **不重连**（重连会互相踢，死循环）
/// - `1013` 服务忙 → 可重连（退避）
///
/// 语音一律走这里（学生端共通约束第 7 条）：业务代码不许自己 `WebSocket.connect`。
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../storage/local_store.dart';
import '../storage/server_store.dart';

/// 关闭码。
abstract final class WsCloseCode {
  /// 认证失败（token 无效/过期）。
  static const int unauthorized = 4001;

  /// 同账号新连接踢掉旧连接。
  static const int kicked = 4002;

  /// 服务忙（超全局并发）。
  static const int tryAgainLater = 1013;

  /// 正常关闭。
  static const int normal = 1000;
}

/// 连接状态。
enum WsState {
  idle,
  connecting,
  connected,
  reconnecting,
  closed,

  /// 认证失败（需重新登录）。
  unauthorized,

  /// 被踢（同账号在别处打开）。
  kicked,
}

/// 重连原因（给上层决定"是否降级到键盘"）。
enum WsDropReason {
  /// 从未连上（服务不可用）。
  cannotConnect,

  /// 连上后断开且重连失败。
  lost,

  /// 服务忙。
  busy,

  /// 主动关闭（不算异常）。
  manual,
}

/// 任务类型（契约 §6.3：`task_type` 决定语种与判定链路，**服务端说了算**）。
abstract final class WsTaskType {
  static const word = 'word';
  static const spell = 'spell';
  static const zhAnswer = 'zh_answer';
  static const recite = 'recite';
}

/// 一次识别任务的参数。
@immutable
class WsStartOptions {
  const WsStartOptions({
    required this.sessionId,
    required this.taskType,
    this.expected,
    this.answerId,
    this.recitationId,
    this.wordId,
    this.enableIntermediate = true,
  });

  final int sessionId;
  final String taskType;

  /// 标准答案：**只发给服务端留存，服务端绝不下发云厂商**（契约 F10）。
  final String? expected;

  final int? answerId;
  final int? recitationId;
  final int? wordId;
  final bool enableIntermediate;

  Map<String, dynamic> toJson() => {
        'type': 'start',
        'session_id': sessionId,
        'task_type': taskType,
        if (expected != null) 'expected': expected,
        if (answerId != null) 'answer_id': answerId,
        if (recitationId != null) 'recitation_id': recitationId,
        if (wordId != null) 'word_id': wordId,
        'enable_intermediate': enableIntermediate,
      };
}

/// 语音长连接客户端。
class WsClient {
  WsClient({required this.serverStore, required this.localStore});

  final ServerStore serverStore;
  final LocalStore localStore;

  /// 断线重连退避（规格 F11 明确要求 1/2/4 秒）。
  static const List<Duration> retryBackoff = [
    Duration(seconds: 1),
    Duration(seconds: 2),
    Duration(seconds: 4),
  ];

  /// 客户端心跳间隔（服务端 60s 无消息即断，这里留足余量）。
  static const Duration heartbeatInterval = Duration(seconds: 20);

  final _messages = StreamController<Map<String, dynamic>>.broadcast();
  final _states = StreamController<WsState>.broadcast();

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _sub;
  Timer? _heartbeat;
  WsState _state = WsState.idle;
  bool _manualClose = false;
  int? _lastAnswerId;

  /// 服务端下发的消息（`started` / `partial` / `final` / `verdict` / `error` / `pong`）。
  Stream<Map<String, dynamic>> get messages => _messages.stream;

  Stream<WsState> get states => _states.stream;

  WsState get state => _state;

  bool get isConnected => _state == WsState.connected;

  /// 重连彻底失败时的回调 —— 上层据此降级（BE-06 F15 的 `action=keyboard`）。
  void Function(WsDropReason reason)? onDropped;

  /// 认证失败（需重新登录）。
  void Function()? onUnauthorized;

  /// 建立连接（含 3 次退避重连）。
  ///
  /// [lastAnswerId] 用于断线重连时**补发 verdict**（后端 F13 / 客户端 U12）：
  /// 学生答完但没收到判定就断线了，重连后凭它把结果捞回来。
  Future<void> connect({int? lastAnswerId}) async {
    _manualClose = false;
    _lastAnswerId = lastAnswerId ?? _lastAnswerId;
    _setState(WsState.connecting);

    for (var attempt = 0; attempt <= retryBackoff.length; attempt++) {
      if (_manualClose) {
        return;
      }
      final ok = await _open();
      if (ok) {
        return;
      }
      if (_state == WsState.unauthorized || _state == WsState.kicked) {
        return; // 这两类重连无意义
      }
      if (attempt < retryBackoff.length) {
        _setState(WsState.reconnecting);
        await Future<void>.delayed(retryBackoff[attempt]);
      }
    }

    _setState(WsState.closed);
    onDropped?.call(WsDropReason.cannotConnect);
  }

  Future<bool> _open() async {
    final base = serverStore.current;
    if (base == null) {
      _setState(WsState.closed);
      return false;
    }
    final token = localStore.token ?? '';
    final uri = base.wsUri('/ws/asr', {
      'token': token,
      if (_lastAnswerId != null) 'last_answer_id': '$_lastAnswerId',
    });

    try {
      await _teardown();
      final channel = WebSocketChannel.connect(uri);
      await channel.ready;
      _channel = channel;

      _sub = channel.stream.listen(
        _onFrame,
        onError: (Object e) => _onClosed(null, e),
        onDone: () => _onClosed(channel.closeCode, channel.closeReason),
        cancelOnError: true,
      );
      _setState(WsState.connected);
      _startHeartbeat();
      return true;
    } catch (e) {
      await _teardown();
      return false;
    }
  }

  void _onFrame(dynamic frame) {
    if (frame is! String) {
      return; // 服务端目前只发文本帧
    }
    Map<String, dynamic>? msg;
    try {
      final decoded = jsonDecode(frame);
      if (decoded is Map<String, dynamic>) {
        msg = decoded;
      }
    } catch (_) {
      return;
    }
    if (msg == null) {
      return;
    }

    // 记下 answer_id：万一这一题判完就断线，重连时能补发 verdict
    final answerId = msg['answer_id'];
    if (answerId is int) {
      _lastAnswerId = answerId;
    }
    if (!_messages.isClosed) {
      _messages.add(msg);
    }
  }

  void _onClosed(int? code, Object? reason) {
    _stopHeartbeat();
    _channel = null;
    final wasManual = _manualClose;

    if (wasManual || code == WsCloseCode.normal) {
      _setState(WsState.closed);
      return;
    }
    if (code == WsCloseCode.unauthorized) {
      _setState(WsState.unauthorized);
      onUnauthorized?.call();
      return;
    }
    if (code == WsCloseCode.kicked) {
      _setState(WsState.kicked);
      onDropped?.call(WsDropReason.lost);
      return;
    }

    // 还有重连机会就交给 connect() 的退避；否则报降级
    if (_state != WsState.reconnecting) {
      _setState(WsState.closed);
      unawaited(_reconnect());
    }
  }

  Future<void> _reconnect() async {
    if (_manualClose) {
      return;
    }
    _setState(WsState.reconnecting);
    for (var attempt = 0; attempt < retryBackoff.length; attempt++) {
      await Future<void>.delayed(retryBackoff[attempt]);
      if (_manualClose) {
        return;
      }
      if (await _open()) {
        return;
      }
      if (_state == WsState.unauthorized || _state == WsState.kicked) {
        return;
      }
    }
    _setState(WsState.closed);
    onDropped?.call(WsDropReason.lost);
  }

  // -------------------------------------------------------------- 发送

  /// 发送 `start`。
  void sendStart(WsStartOptions options) {
    if (options.answerId != null) {
      _lastAnswerId = options.answerId;
    }
    _sendJson(options.toJson());
  }

  /// 发送一包音频（**二进制帧**，裸 PCM 16k/单声道/16bit）。
  bool sendAudio(Uint8List chunk) {
    final channel = _channel;
    if (channel == null || chunk.isEmpty) {
      return false;
    }
    try {
      channel.sink.add(chunk);
      return true;
    } catch (_) {
      return false;
    }
  }

  /// 结束本次录音（服务端收到后产出 final 并判定）。
  void sendEnd() => _sendJson({'type': 'end'});

  /// 放弃本次识别（**不产 final、不判分**，用于学生中途退出）。
  void sendCancel() => _sendJson({'type': 'cancel'});

  /// 心跳。
  void sendPing() => _sendJson({'type': 'ping'});

  void _sendJson(Map<String, dynamic> payload) {
    final channel = _channel;
    if (channel == null) {
      return;
    }
    try {
      channel.sink.add(jsonEncode(payload));
    } catch (_) {
      // 连接可能刚好断了；重连流程会兜住，这里不抛
    }
  }

  // -------------------------------------------------------------- 生命周期

  void _startHeartbeat() {
    _stopHeartbeat();
    _heartbeat = Timer.periodic(heartbeatInterval, (_) => sendPing());
  }

  void _stopHeartbeat() {
    _heartbeat?.cancel();
    _heartbeat = null;
  }

  void _setState(WsState state) {
    if (_state == state) {
      return;
    }
    _state = state;
    if (!_states.isClosed) {
      _states.add(state);
    }
  }

  Future<void> _teardown() async {
    _stopHeartbeat();
    await _sub?.cancel();
    _sub = null;
    try {
      await _channel?.sink.close();
    } catch (_) {
      // 已断开时 close 会抛，忽略
    }
    _channel = null;
  }

  /// 主动关闭（退出练习页时调）。
  Future<void> close() async {
    _manualClose = true;
    await _teardown();
    _setState(WsState.closed);
  }

  /// 释放全部资源（App 退出/Provider dispose）。
  Future<void> dispose() async {
    await close();
    await _messages.close();
    await _states.close();
  }
}
