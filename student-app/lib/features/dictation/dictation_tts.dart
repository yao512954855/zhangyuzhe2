/// 听写 TTS 封装 —— flutter_tts 的薄接口层。
///
/// 为什么不直接在页面里 new FlutterTts()：flutter_tts 全是平台通道调用，
/// widget 测试里一跑就 MissingPluginException。抽一个接口，页面只认
/// [DictationTts]，测试注入 fake（见 test/test_wp_d_dictation_test.dart）。
///
/// 语言按播报类型选（契约 §8）：报英文 en-US、报中文 zh-CN。
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_tts/flutter_tts.dart';

/// TTS 能力接口（页面与测试都只依赖它）。
abstract interface class DictationTts {
  /// 设置朗读语言（'zh-CN' / 'en-US'）。
  Future<void> setLanguage(String language);

  /// 朗读文本；awaitSpeakCompletion 开启时 future 在读完才完成。
  Future<void> speak(String text);

  /// 停止当前朗读。
  Future<void> stop();

  /// 释放资源（页面 dispose 时调用）。
  Future<void> dispose();
}

/// flutter_tts 默认实现。
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
  Future<void> speak(String text) async {
    await _ensureConfigured();
    await _tts.speak(text);
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

/// 听写 TTS 提供者（页面默认从这里取；测试 override 成 fake）。
final dictationTtsProvider = Provider<DictationTts>((ref) {
  final tts = FlutterTtsAdapter();
  ref.onDispose(tts.dispose);
  return tts;
});
