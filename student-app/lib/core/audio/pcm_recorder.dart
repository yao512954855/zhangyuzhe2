/// 录音与 PCM 分片 —— 30-APP-01 F12（U13）。
///
/// **格式必须与云端要求逐项一致，否则识别恒为空**：
/// `16kHz / 单声道 / 16bit / PCM`，按 **150ms** 一包推给 WebSocket。
/// 这是整个语音链路上最容易"看起来跑通了但永远识别为空"的地方，
/// 所以 [PcmChunker] 单独抽出来做纯逻辑，并由单测钉死字节数。
///
/// **录音默认不保存**（项目硬约定）：用 `startStream` 取流，不落盘、不产生文件。
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';

/// 麦克风权限状态。
enum MicPermission {
  granted,

  /// 本次拒绝（还能再问）。
  denied,

  /// 永久拒绝（只能去系统设置里开）。
  permanentlyDenied,
}

/// PCM 参数与分片器（**纯逻辑，可单测**）。
abstract final class PcmSpec {
  static const int sampleRate = 16000;
  static const int channels = 1;
  static const int bitsPerSample = 16;
  static const int chunkMs = 150;

  /// 每包字节数 = 16000 × (16/8) × 0.15 = **4800**。
  static const int chunkBytes = sampleRate * (bitsPerSample ~/ 8) * chunkMs ~/ 1000;

  /// 单次录音最长时长（UI 规范 §6.4：30 秒自动结束，避免长时间对着麦克风）。
  static const int maxRecordSeconds = 30;

  /// 字节数 → 毫秒。
  static int msOf(int byteLength) =>
      byteLength * 1000 ~/ (sampleRate * (bitsPerSample ~/ 8));
}

/// 把不定长的音频回调拼成固定 150ms 的包。
///
/// 系统给的回调大小完全不可控（有的 1024 字节、有的 4096，还可能乱序大小），
/// **不能假设一次回调就是一包** —— 直接透传会让服务端拿到长度失控的帧。
class PcmChunker {
  PcmChunker({this.chunkBytes = PcmSpec.chunkBytes});

  final int chunkBytes;

  final List<int> _buffer = <int>[];

  /// 缓存中的字节数（调试/测试用）。
  int get pending => _buffer.length;

  /// 投入一段音频，返回已经凑够整包的切片（可能 0 个或多个）。
  List<Uint8List> add(List<int> data) {
    if (data.isEmpty) {
      return const [];
    }
    _buffer.addAll(data);
    final out = <Uint8List>[];
    while (_buffer.length >= chunkBytes) {
      out.add(Uint8List.fromList(_buffer.sublist(0, chunkBytes)));
      _buffer.removeRange(0, chunkBytes);
    }
    return out;
  }

  /// 收尾：把不足一包的残余吐出来（`end` 之前必须调，否则丢掉最后一点声音）。
  Uint8List? flush() {
    if (_buffer.isEmpty) {
      return null;
    }
    final tail = Uint8List.fromList(_buffer);
    _buffer.clear();
    return tail;
  }
}

/// 录音器。
class PcmRecorder {
  PcmRecorder([AudioRecorder? recorder]) : _recorder = recorder ?? AudioRecorder();

  final AudioRecorder _recorder;
  StreamSubscription<Uint8List>? _sub;
  bool _recording = false;

  bool get isRecording => _recording;

  /// 申请麦克风权限。
  ///
  /// 被拒后**不要反复弹窗**（30-APP-01 关键要点）：把结果交给 UI，
  /// 由 UI 给一次说明 + 「去设置」按钮。
  Future<MicPermission> ensurePermission() async {
    final status = await Permission.microphone.request();
    if (status.isGranted || status.isLimited) {
      return MicPermission.granted;
    }
    if (status.isPermanentlyDenied || status.isRestricted) {
      return MicPermission.permanentlyDenied;
    }
    return MicPermission.denied;
  }

  /// 当前是否已有权限（不弹窗）。
  Future<bool> hasPermission() async {
    return Permission.microphone.isGranted;
  }

  /// 跳到系统设置页（权限被永久拒绝时用）。
  Future<bool> openSettings() => openAppSettings();

  /// 开始录音，返回**已按 150ms 切片**的音频流。
  ///
  /// 出错抛 [StateError]（调用方负责转成中文提示）。
  Future<Stream<Uint8List>> start() async {
    if (_recording) {
      throw StateError('已经在录音了');
    }
    final stream = await _recorder.startStream(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: PcmSpec.sampleRate,
        numChannels: PcmSpec.channels,
        // 以下三项**保持关闭**：ASR 预研尚未测过这组开关对识别率的影响，
        // 未实测前不开，避免与预研结论对不上（需求 §6 判分口径依赖预研）。
        autoGain: false,
        echoCancel: false,
        noiseSuppress: false,
      ),
    );
    _recording = true;

    final chunker = PcmChunker();
    final controller = StreamController<Uint8List>();
    _sub = stream.listen(
      (data) {
        for (final chunk in chunker.add(data)) {
          controller.add(chunk);
        }
      },
      onError: controller.addError,
      onDone: () {
        final tail = chunker.flush();
        if (tail != null) {
          controller.add(tail);
        }
        controller.close();
      },
      cancelOnError: false,
    );
    return controller.stream;
  }

  /// 停止录音（幂等）。
  Future<void> stop() async {
    if (!_recording) {
      return;
    }
    _recording = false;
    await _sub?.cancel();
    _sub = null;
    try {
      await _recorder.stop();
    } catch (_) {
      // 已经停了，忽略
    }
  }

  /// 释放资源。
  Future<void> dispose() async {
    await stop();
    await _recorder.dispose();
  }
}
