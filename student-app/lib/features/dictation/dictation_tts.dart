/// 听写 TTS 封装 —— 设备 TTS 优先、云端兜底（契约 v1.2）。
///
/// 为什么不直接在页面里 new FlutterTts()：flutter_tts 全是平台通道调用，
/// widget 测试里一跑就 MissingPluginException。抽一个接口，页面只认
/// [DictationTts]，测试注入 fake（见 test/test_wp_d_dictation_test.dart）。
///
/// 为什么需要云端兜底：部分国产 ROM（实测小米/MIUI）**不预装任何标准
/// TTS 引擎**，speak 直接 `not bound to TTS engine`（flutter_tts 返回非 1）。
/// [ResilientTts] 先试设备，失败自动回退后端 `POST /api/tts` 合成的
/// wav（audioplayers 播放），并在本次会话内记住「设备不可用」避免逐题重试。
library;

import 'dart:async';
import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:path_provider/path_provider.dart';

import '../../core/providers.dart';
import '../../core/storage/server_store.dart';

/// TTS 能力接口（页面与测试都只依赖它）。
abstract interface class DictationTts {
  /// 设置朗读语言（'zh-CN' / 'en-US'）。
  Future<void> setLanguage(String language);

  /// 设置语速（0.0~1.0，todo13 #5：来自管理端 dictation_speech_rate/100）。
  Future<void> setSpeechRate(double rate);

  /// 朗读文本；awaitSpeakCompletion 开启时 future 在读完才完成。
  Future<void> speak(String text);

  /// 停止当前朗读。
  Future<void> stop();

  /// 释放资源（页面 dispose 时调用）。
  Future<void> dispose();
}

/// 设备 TTS 不可用（引擎缺失/绑定失败/ speak 返回非成功）。
class DeviceTtsUnavailable implements Exception {
  const DeviceTtsUnavailable(this.reason);

  final String reason;

  @override
  String toString() => 'DeviceTtsUnavailable: $reason';
}

/// flutter_tts 设备实现。
class FlutterTtsAdapter implements DictationTts {
  FlutterTtsAdapter() : _tts = FlutterTts();

  final FlutterTts _tts;
  bool _configured = false;

  Future<void> _ensureConfigured() async {
    if (_configured) {
      return;
    }
    // 朗读完成后 speak 的 future 才完成 —— 播报页靠这个衔接「间隔计时」。
    await _tts.awaitSpeakCompletion(true);
    _configured = true;
  }

  @override
  Future<void> setLanguage(String language) async {
    await _ensureConfigured();
    try {
      await _tts.setLanguage(language);
    } catch (_) {
      // 个别机型缺语言包会抛错；不阻断流程（系统会退回默认语言）。
    }
  }

  @override
  Future<void> setSpeechRate(double rate) async {
    await _ensureConfigured();
    try {
      await _tts.setSpeechRate(rate.clamp(0.0, 1.0));
    } catch (_) {
      // 个别机型引擎不支持语速调节；不阻断流程（退回引擎默认语速）。
    }
  }

  @override
  Future<void> speak(String text) async {
    await _ensureConfigured();
    // ⚠️ 引擎绑不上时（无 TTS 引擎的国产 ROM），awaitSpeakCompletion 模式下
    // flutter_tts 的 speak future 可能**永不完成** —— 必须加超时把这类机型
    // 转成 DeviceTtsUnavailable，ResilientTts 才有机会回退云端。
    final Object result = await _tts
        .speak(text)
        .timeout(
          const Duration(seconds: 4),
          onTimeout: () => throw const DeviceTtsUnavailable('speak 超时（引擎未绑定）'),
        );
    // Android 引擎未绑定/缺引擎时返回非 1（logcat: not bound to TTS engine）。
    if (result is num && result != 1) {
      throw DeviceTtsUnavailable('speak 返回 $result');
    }
  }

  @override
  Future<void> stop() async {
    try {
      await _tts.stop();
    } catch (_) {
      // 未在朗读时 stop 在部分平台会抛错，静默忽略。
    }
  }

  @override
  Future<void> dispose() => stop();
}

/// 云端 TTS 播放器 —— `POST /api/tts` 取 wav 落临时文件再用 audioplayers 播。
///
/// 同一文本按 hashCode 缓存在临时目录（会话级），重听零网络请求。
class CloudTtsPlayer implements DictationTts {
  CloudTtsPlayer({required ServerStore serverStore, Dio? dio})
      : _serverStore = serverStore,
        _dio = dio ??
            Dio(
              BaseOptions(
                connectTimeout: const Duration(seconds: 10),
                receiveTimeout: const Duration(seconds: 15),
                responseType: ResponseType.bytes,
                validateStatus: (status) => status != null && status < 600,
              ),
            );

  final ServerStore _serverStore;
  final Dio _dio;
  final AudioPlayer _player = AudioPlayer();

  /// 抢占信号：新的 speak/stop 会打断在途的 await 朗读完成。
  Completer<void>? _preempt;
  final Map<String, String> _fileCache = {};

  /// 播报语言（'en-US'/'zh-CN'，随听写设置走），缓存键也含它。
  String _lang = 'zh-CN';

  @override
  Future<void> setLanguage(String language) async {
    _lang = language;
  }

  @override
  Future<void> setSpeechRate(double rate) async {
    // 云端合成走服务端 qwen3-tts（无语速参数下发，todo13 #5 范围外）；
    // 设备不可用属兜底场景，保持引擎默认语速即可。
  }

  @override
  Future<void> speak(String text) async {
    _preempt?.complete();
    final preempt = _preempt = Completer<void>();
    final path = await _ensureCached(text);
    if (preempt.isCompleted) {
      return; // 已被新的 speak/stop 抢占
    }
    await _player.stop();
    if (preempt.isCompleted) {
      return;
    }
    await _player.play(DeviceFileSource(path));
    // 自然播完 or 被抢占（stop/重听/退出）二者取其一。
    await Future.any<void>([
      _player.onPlayerComplete.first,
      preempt.future,
    ]);
  }

  @override
  Future<void> stop() async {
    _preempt?.complete();
    _preempt = null;
    try {
      await _player.stop();
    } catch (_) {
      // 未在播放时 stop 可能抛错，静默忽略。
    }
  }

  @override
  Future<void> dispose() => stop();

  Future<String> _ensureCached(String text) async {
    final cached = _fileCache[text];
    if (cached != null && File(cached).existsSync()) {
      return cached;
    }
    final address = _serverStore.current;
    if (address == null) {
      throw const DeviceTtsUnavailable('未连接服务器');
    }
    final base = address.baseUri;
    final res = await _dio.post<List<int>>(
      base.replace(path: '/api/tts').toString(),
      data: {'text': text, 'lang': _lang},
      options: Options(responseType: ResponseType.bytes),
    );
    final status = res.statusCode ?? 0;
    if (status != 200) {
      throw DeviceTtsUnavailable('云端 TTS HTTP $status');
    }
    final dir = await getTemporaryDirectory();
    final file = File(
      '${dir.path}/tts_${text.hashCode.abs()}_${DateTime.now().millisecondsSinceEpoch % 100000}.wav',
    );
    await file.writeAsBytes(res.data!);
    _fileCache[text] = file.path;
    return file.path;
  }
}

/// 设备优先、云端兜底的组合实现。
class ResilientTts implements DictationTts {
  ResilientTts({required this.device, required this.cloud});

  final DictationTts device;
  final DictationTts cloud;

  /// 设备 TTS 一旦判死，本会话内直接走云端（缺引擎的机型不值得逐题重试）。
  bool _deviceDead = false;

  @override
  Future<void> setLanguage(String language) async {
    await device.setLanguage(language);
    await cloud.setLanguage(language);
  }

  @override
  Future<void> setSpeechRate(double rate) async {
    await device.setSpeechRate(rate);
    await cloud.setSpeechRate(rate);
  }

  @override
  Future<void> speak(String text) async {
    if (!_deviceDead) {
      try {
        await device.speak(text);
        return;
      } on DeviceTtsUnavailable {
        _deviceDead = true;
      }
    }
    await cloud.speak(text);
  }

  @override
  Future<void> stop() async {
    await device.stop();
    await cloud.stop();
  }

  @override
  Future<void> dispose() async {
    await device.dispose();
    await cloud.dispose();
  }
}

/// 听写 TTS 提供者（页面默认从这里取；测试 override 成 fake）。
final dictationTtsProvider = Provider<DictationTts>((ref) {
  final tts = ResilientTts(
    device: FlutterTtsAdapter(),
    cloud: CloudTtsPlayer(serverStore: ref.watch(serverStoreProvider)),
  );
  ref.onDispose(tts.dispose);
  return tts;
});
