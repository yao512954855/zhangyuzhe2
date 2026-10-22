/// ResilientTts 回退逻辑单测（契约 v1.2）：设备 TTS 失败 → 云端兜底。
///
/// 背景：小米/MIUI 等机型无 TTS 引擎，flutter_tts speak 返回非 1。
/// 纯逻辑测试，无 widget、无平台通道。
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/features/dictation/dictation_tts.dart';

// 复用 WP-D 测试里的 FakeTts 会引入整页依赖，这里就地写一个轻量假 TTS。
class _FakeTts implements DictationTts {
  final List<String> spoken = <String>[];
  final List<String> languages = <String>[];
  final List<double> rates = <double>[];
  int stopCount = 0;

  @override
  Future<void> setLanguage(String language) async {
    languages.add(language);
  }

  @override
  Future<void> setSpeechRate(double rate) async {
    rates.add(rate);
  }

  @override
  Future<void> speak(String text) async {
    spoken.add(text);
  }

  @override
  Future<void> stop() async {
    stopCount++;
  }

  @override
  Future<void> dispose() async {}
}

/// ResilientTts 回退逻辑（契约 v1.2）：设备 TTS 失败 → 云端兜底。
///
/// 背景：小米/MIUI 等机型无 TTS 引擎，flutter_tts speak 返回非 1。
void main() {
  test('设备 speak 成功时不走云端', () async {
    final device = _FakeTts();
    final cloud = _FakeTts();
    final tts = ResilientTts(device: device, cloud: cloud);
    await tts.speak('apple');
    expect(device.spoken, ['apple']);
    expect(cloud.spoken, isEmpty);
  });

  test('设备抛 DeviceTtsUnavailable → 本次走云端，并记住设备已死', () async {
    final device = _DeadTts();
    final cloud = _FakeTts();
    final tts = ResilientTts(device: device, cloud: cloud);
    await tts.speak('apple');
    expect(cloud.spoken, ['apple']);
    // 第二次不再碰设备（避免逐题白等绑定失败）
    await tts.speak('banana');
    expect(device.speakCalls, 1);
    expect(cloud.spoken, ['apple', 'banana']);
  });

  test('stop 同时停设备与云端', () async {
    final device = _FakeTts();
    final cloud = _FakeTts();
    final tts = ResilientTts(device: device, cloud: cloud);
    await tts.stop();
    expect(device.stopCount, 1);
    expect(cloud.stopCount, 1);
  });

  test('setLanguage 两路都设置', () async {
    final device = _FakeTts();
    final cloud = _FakeTts();
    final tts = ResilientTts(device: device, cloud: cloud);
    await tts.setLanguage('en-US');
    expect(device.languages, ['en-US']);
    expect(cloud.languages, ['en-US']);
  });
}

/// 永远绑定失败的设备 TTS（模拟无引擎机型）。
class _DeadTts implements DictationTts {
  int speakCalls = 0;

  @override
  Future<void> setLanguage(String language) async {}

  @override
  Future<void> setSpeechRate(double rate) async {}

  @override
  Future<void> speak(String text) async {
    speakCalls++;
    throw const DeviceTtsUnavailable('not bound to TTS engine');
  }

  @override
  Future<void> stop() async {}

  @override
  Future<void> dispose() async {}
}
