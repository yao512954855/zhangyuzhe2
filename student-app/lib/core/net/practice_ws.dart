/// 练习 WS 会话封装 —— 简化版 WP-C（契约冻结 §5 / §6.4，v1.3）。
///
/// 复用既有 `ws_client.dart`（连接/心跳/重连）与 `pcm_recorder.dart`（常驻录音），
/// 只加两层薄薄的东西：
/// - **上行新形状**：`start {session_id, mode, item_id}`（不再用旧
///   `WsStartOptions` 的 `task_type`/int session —— 简化版 practice 的
///   `session_id` 是字符串，按 App 上行原样透传）；
/// - **v1.3 按住说话（press-and-hold）**：按下 [begin] 发 `start` 并开始推流，
///   松开 [endRound] 停推流并发 `end` 触发服务端整词判定。推流用 [_pushing]
///   门控而**不反复 start/stop 录音器**（真机教训：录音实例反复重建会烂掉）；
/// - **下行 `verdict` 解析**（§5 v1.3 新增，替代已废弃的 `letter`）：
///   `{correct, text, session_id, item_id}` → [VerdictEvent]。
///
/// en2zh 模式下服务端走既有 ASR 转写链路，本类把 `partial`/`final`
/// 转成 [TranscriptEvent]；比对在服务端 compare_service，客户端只转发文本。
///
/// 测试注入口：[PracticeWsSession.incoming] 允许组件测试注入假消息流
/// （不连真 WS、不碰麦克风）。
library;

import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../audio/pcm_recorder.dart';
import 'ws_client.dart';

/// 录音器 Provider（原定义在旧 recite_controller 里，随 WP-C 重写迁到这里；
/// 常驻方案：App 生命周期内只建一次，反复 start/stop 是它的正常用法）。
final pcmRecorderProvider = Provider<PcmRecorder>((ref) {
  return PcmRecorder();
});

/// 服务端 `verdict` 下行（§5 v1.3）：松开后服务端整词判定结果。
///
/// 判定口径服务端唯一：整段转写经 judge 谐音映射还原为字母序列，与
/// `item.en`（去空格/连字符）完全一致 = 对。App 只消费它渲染绿/红。
@immutable
class VerdictEvent {
  const VerdictEvent({
    required this.correct,
    required this.text,
    required this.sessionId,
    required this.itemId,
  });

  factory VerdictEvent.fromJson(Map<String, dynamic> json) => VerdictEvent(
        correct: json['correct'] == true,
        text: json['text']?.toString() ?? '',
        sessionId: json['session_id']?.toString() ?? '',
        itemId: (json['item_id'] as num?)?.toInt() ?? 0,
      );

  final bool correct;

  /// 参与判定的整段转写文本（识别内容区定版显示用它）。
  final String text;
  final String sessionId;
  final int itemId;
}

/// 服务端转写下行（`partial` / `final`）：en2zh 模式学生说的中文；
/// zh2en 模式按住中 `partial` 实时回显（决策 #11：识别字母实时上屏）。
@immutable
class TranscriptEvent {
  const TranscriptEvent({required this.text, required this.isFinal});

  final String text;
  final bool isFinal;
}

/// 服务端 `utterance` 下行（契约 §5）：Vosk 语句级定版结果，仅上屏不判格
/// （v1.3：逐字母判格已废除，判定只看松开后的 verdict）。
@immutable
class UtteranceEvent {
  const UtteranceEvent({required this.text, required this.itemId});

  factory UtteranceEvent.fromJson(Map<String, dynamic> json) => UtteranceEvent(
        text: json['text']?.toString() ?? '',
        itemId: (json['item_id'] as num?)?.toInt() ?? 0,
      );

  final String text;
  final int itemId;
}

/// 服务端 `error` 下行：start 被拒 / 没听到声音等（§5）。
///
/// 页面收到后必须退出「正在听」态并把按钮还给用户 —— 否则就是死页面
/// （2026-10-11 真机踩坑：start 被拒后 App 一直推音频，界面无任何反馈）。
@immutable
class AsrErrorEvent {
  const AsrErrorEvent({
    required this.code,
    required this.message,
    this.action,
  });

  factory AsrErrorEvent.fromJson(Map<String, dynamic> json) => AsrErrorEvent(
        code: (json['code'] as num?)?.toInt() ?? 0,
        message: json['message']?.toString() ?? '语音识别出了点问题',
        action: json['action']?.toString(),
      );

  final int code;
  final String message;

  /// `abort` / `retry` / `keyboard`（§5）：abort 必须停推流。
  final String? action;
}

/// 一次拼读/说中文的 WS 会话。
///
/// 页面生命周期对象：一个答题页建一个，退出时 [close]。
class PracticeWsSession {
  PracticeWsSession({
    required WsClient wsClient,
    required PcmRecorder micRecorder,
    Stream<Map<String, dynamic>>? serverMessages,
    Stream<WsState>? serverStates,

    /// 测试开关：false 时 begin 不再发起连接（注入消息流直驱，避免测试里
    /// 真 WS 重连挂起 Timer）；生产保持默认 true。
    this.autoConnect = true,
  })  : _ws = wsClient,
        _recorder = micRecorder,
        _incoming = serverMessages,
        _stateStream = serverStates {
    // 注入消息流（测试/嵌入场景）在构造时即订阅：不依赖 begin() 才开始解析。
    if (_incoming != null) {
      _msgSub = _incoming.listen(_onMessage);
    }
  }

  final bool autoConnect;

  final WsClient _ws;
  final PcmRecorder _recorder;

  /// 测试注入口：给定时不再从 `ws.messages` 取消息。
  final Stream<Map<String, dynamic>>? _incoming;

  /// 测试注入口：连接状态流（断线转暂停的驱动源）。
  final Stream<WsState>? _stateStream;

  StreamSubscription<Map<String, dynamic>>? _msgSub;
  StreamSubscription<Uint8List>? _audioSub;

  /// 按住推流门控（v1.3）：true = 音频帧上送 WS；false = 静默丢弃。
  /// 按下置 true、松开/判错置 false —— 录音器本身常驻不重建，
  /// 避免反复 start/stop 把底层实例弄坏（2026-10-01 真机教训）。
  bool _pushing = false;

  final _verdicts = StreamController<VerdictEvent>.broadcast();
  final _transcripts = StreamController<TranscriptEvent>.broadcast();
  final _utterances = StreamController<UtteranceEvent>.broadcast();
  final _errors = StreamController<AsrErrorEvent>.broadcast();

  /// 整词判定事件（§5 v1.3）：松开后服务端唯一判定，页面渲染绿/红与倒计时。
  Stream<VerdictEvent> get verdicts => _verdicts.stream;

  /// 转写事件（en2zh 展示 / zh2en 实时 partial 展示用）。
  Stream<TranscriptEvent> get transcripts => _transcripts.stream;

  /// 语句级定版结果（回显展示用；v1.3 不再驱动判格）。
  Stream<UtteranceEvent> get utterances => _utterances.stream;

  /// 服务端报错事件（start 被拒 / 空结果等），页面据此退出录音态。
  Stream<AsrErrorEvent> get errors => _errors.stream;

  Stream<WsState> get states => _stateStream ?? _ws.states;

  WsState get state => _ws.state;

  bool get isCapturing => _recorder.isRecording;

  /// 麦克风音量电平（0~1，录音期间每帧约 150ms 一个事件）。
  /// 供答题页做「正在听你说」动效；未录音时无事件。
  Stream<double> get micLevels => _recorder.levelStream;

  /// 本会话是否已收到过服务端下行（测试/调试用）。
  bool gotServerMessage = false;

  /// 连接 + 发 `start` + 开始推音频（v1.3：按下大圆钮）。
  ///
  /// 返回 false = 权限被拒或通道起不来（上层给明确提示，不硬开录音）。
  Future<bool> begin({
    required String sessionId,
    required String mode,
    required int itemId,
  }) async {
    _msgSub ??= (_incoming ?? _ws.messages).listen(_onMessage);
    // 麦克风权限（旧链路在页面层申请，随重写收进封装；拒权限/无插件环境
    // 都不算异常，统一按「语音没就绪」返回 false）。
    final MicPermission perm;
    try {
      perm = await _recorder.ensurePermission();
    } on Object {
      return false;
    }
    if (perm != MicPermission.granted) {
      return false;
    }
    if (autoConnect && !_ws.isConnected) {
      await _ws.connect();
      if (_ws.state != WsState.connected) {
        return false;
      }
    }
    _sendStart(sessionId: sessionId, mode: mode, itemId: itemId);

    if (!_recorder.isRecording) {
      try {
        final stream = await _recorder.start();
        // 常驻录音 + 推流门控：订阅保持到会话结束，帧是否上送由 [_pushing]
        // 决定（按住=true，松开/判错=false）。订阅只建一次 —— 单订阅流
        // 取消后无法重新 listen，反复按住靠门控而不是重订。
        _audioSub ??= stream.listen(
          (chunk) {
            if (_pushing) {
              _ws.sendAudio(chunk);
            }
          },
          onError: (Object _) {},
          cancelOnError: false,
        );
      } on Object catch (e) {
        debugPrint('[practice-ws] 录音启动失败：$e');
        return false;
      }
    }
    _pushing = true;
    return true;
  }

  void _sendStart({
    required String sessionId,
    required String mode,
    required int itemId,
  }) {
    // 契约 §5：信封不变（{type, ...}），session_id 按 App 上行原样透传。
    _ws.sendRawJson(<String, dynamic>{
      'type': 'start',
      'session_id': sessionId,
      'mode': mode,
      'item_id': itemId,
    });
  }

  /// 松开大圆钮（v1.3）：停推流并发 `end`，服务端收口本轮整词判定。
  void endRound() {
    _pushing = false;
    _ws.sendEnd();
  }

  /// 停推流（判错抑制，§5：verdict(correct=false) 后 App 停推流；
  /// 服务端同时忽略本轮后续帧，双保险）。连接与录音保留。
  void stopPush() => _pushing = false;

  /// 服务端报错（尤其 abort）后彻底停录音；连接保留，页面可重试。
  Future<void> stopCapture() async {
    _pushing = false;
    await _audioSub?.cancel();
    _audioSub = null;
    await _recorder.stop();
  }

  void _onMessage(Map<String, dynamic> msg) {
    gotServerMessage = true;
    final type = msg['type']?.toString() ?? '';
    switch (type) {
      case 'verdict':
        final event = VerdictEvent.fromJson(msg);
        if (!_verdicts.isClosed) {
          _verdicts.add(event);
        }
      case 'utterance':
        final event = UtteranceEvent.fromJson(msg);
        if (event.text.isNotEmpty && !_utterances.isClosed) {
          _utterances.add(event);
        }
      case 'error':
        if (!_errors.isClosed) {
          _errors.add(AsrErrorEvent.fromJson(msg));
        }
      case 'partial':
      case 'final':
      case 'result':
        final text =
            (msg['text'] ?? msg['partial'] ?? msg['result'])?.toString() ?? '';
        if (text.isEmpty) {
          return;
        }
        if (!_transcripts.isClosed) {
          _transcripts.add(
            TranscriptEvent(text: text, isFinal: type == 'final'),
          );
        }
      default:
        // started / pong 等由 WsClient 或页面自行处理
        break;
    }
  }

  /// 断线检测：上层轮询/订阅 [states] 转「暂停」态，不清作答进度（§5）。
  bool get isDisconnected =>
      _ws.state == WsState.closed || _ws.state == WsState.reconnecting;

  /// 退出答题页：停录音、断连接、关流。
  Future<void> close() async {
    _pushing = false;
    await _audioSub?.cancel();
    _audioSub = null;
    _recorder.stop();
    await _msgSub?.cancel();
    _msgSub = null;
    await _ws.close();
    await _verdicts.close();
    await _transcripts.close();
    await _utterances.close();
    await _errors.close();
  }
}
