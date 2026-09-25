/// WP-APP-01 录音格式与护眼提醒 —— U12 / U13。
library;

import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/audio/pcm_recorder.dart';
import 'package:student_app/core/eye_care/eye_care_service.dart';

void main() {
  group('PCM 规格（U13：16k / 单声道 / 16bit / 150ms）', () {
    test('采样参数与云端要求一致', () {
      expect(PcmSpec.sampleRate, 16000);
      expect(PcmSpec.channels, 1);
      expect(PcmSpec.bitsPerSample, 16);
      expect(PcmSpec.chunkMs, 150);
    });

    test('每包字节数 = 16000 × 2 × 0.15 = 4800', () {
      expect(PcmSpec.chunkBytes, 4800);
      expect(
        PcmSpec.sampleRate * (PcmSpec.bitsPerSample ~/ 8) * PcmSpec.chunkMs ~/ 1000,
        PcmSpec.chunkBytes,
      );
    });

    test('字节 ↔ 时长换算', () {
      expect(PcmSpec.msOf(PcmSpec.chunkBytes), 150);
      expect(PcmSpec.msOf(PcmSpec.chunkBytes * 4), 600);
      expect(PcmSpec.msOf(32000), 1000);
      expect(PcmSpec.msOf(0), 0);
    });

    test('单次录音上限 30 秒（UI 规范 §6.4）', () {
      expect(PcmSpec.maxRecordSeconds, 30);
    });
  });

  group('PcmChunker：把不定长回调拼成固定 150ms 包', () {
    Uint8List bytes(int n, [int seed = 0]) =>
        Uint8List.fromList(List<int>.generate(n, (i) => (i + seed) % 256));

    test('正好一包', () {
      final chunker = PcmChunker();
      final out = chunker.add(bytes(4800));
      expect(out.length, 1);
      expect(out.single.length, 4800);
      expect(chunker.pending, 0);
    });

    test('一次给两包就出两包', () {
      final chunker = PcmChunker();
      final out = chunker.add(bytes(9600));
      expect(out.length, 2);
      expect(out.every((c) => c.length == 4800), isTrue);
      expect(chunker.pending, 0);
    });

    test('小回调逐步累积（系统回调大小不可控，不能假设一次回调就是一包）', () {
      final chunker = PcmChunker();
      var produced = 0;
      for (var i = 0; i < 5; i++) {
        produced += chunker.add(bytes(1000)).length; // 5000 字节
      }
      expect(produced, 1, reason: '5000 字节只够 1 包');
      expect(chunker.pending, 200, reason: '余 200 字节等下个回调');
    });

    test('剩余字节在 flush 时发出（不丢最后一点声音）', () {
      final chunker = PcmChunker();
      chunker.add(bytes(5000));
      final tail = chunker.flush();
      expect(tail, isNotNull);
      expect(tail!.length, 200);
      expect(chunker.pending, 0);
      expect(chunker.flush(), isNull, reason: 'flush 幂等');
    });

    test('空输入不产生任何包', () {
      final chunker = PcmChunker();
      expect(chunker.add(const <int>[]), isEmpty);
      expect(chunker.flush(), isNull);
    });

    test('分片内容按顺序拼接，不串位', () {
      final chunker = PcmChunker(chunkBytes: 4);
      final out = chunker.add(const [1, 2, 3, 4, 5, 6, 7, 8, 9]);
      expect(out.length, 2);
      expect(out[0], [1, 2, 3, 4]);
      expect(out[1], [5, 6, 7, 8]);
      expect(chunker.flush(), [9]);
    });

    test('大数据流（10 秒音频 = 320000 字节）产出 66 包 + 尾包', () {
      final chunker = PcmChunker();
      final out = chunker.add(bytes(320000));
      expect(out.length, 66, reason: '320000 / 4800 = 66.67');
      final tail = chunker.flush();
      expect(tail!.length, 320000 - 66 * 4800);
    });
  });

  group('护眼提醒（U12）', () {
    test('累计到 20 分钟触发一次', () async {
      final service = EyeCareService();
      expect(service.restInterval, const Duration(minutes: 20));
      expect(service.accumulated, Duration.zero);
      expect(service.restDue, isFalse);

      service.tick(const Duration(minutes: 19));
      expect(service.restDue, isFalse, reason: '19 分钟还不该提醒');

      service.tick(const Duration(minutes: 1));
      expect(service.restDue, isTrue);
    });

    test('触发时发出 onRestDue 事件', () async {
      final service = EyeCareService();
      final events = <void>[];
      final sub = service.onRestDue.listen(events.add);

      service.tick(const Duration(minutes: 20));
      await Future<void>.delayed(Duration.zero);
      expect(events.length, 1);

      // 已经弹过一次，不重复触发（否则会连弹）
      service.tick(const Duration(minutes: 20));
      await Future<void>.delayed(Duration.zero);
      expect(events.length, 1);

      await sub.cancel();
      service.dispose();
    });

    test('「我休息好了」以后重新计时', () {
      final service = EyeCareService();
      service.tick(const Duration(minutes: 20));
      expect(service.restDue, isTrue);

      service.markRested();
      expect(service.restDue, isFalse);
      expect(service.accumulated, Duration.zero);

      service.tick(const Duration(minutes: 10));
      expect(service.restDue, isFalse);
      service.dispose();
    });

    test('关闭开关后不再累计（且立即清零）', () {
      final service = EyeCareService();
      service.tick(const Duration(minutes: 10));
      expect(service.accumulated, const Duration(minutes: 10));

      service.setEnabled(false);
      expect(service.accumulated, Duration.zero, reason: '不清零会导致一开就弹');
      service.tick(const Duration(minutes: 30));
      expect(service.restDue, isFalse);
      service.dispose();
    });

    test('从构造就关闭时 tick 无效', () {
      final service = EyeCareService(enabled: false);
      service.tick(const Duration(minutes: 60));
      expect(service.accumulated, Duration.zero);
      expect(service.restDue, isFalse);
      service.dispose();
    });

    test('休息间隔可配置', () {
      final service = EyeCareService(restInterval: const Duration(minutes: 5));
      service.tick(const Duration(minutes: 4, seconds: 59));
      expect(service.restDue, isFalse);
      service.tick(const Duration(seconds: 1));
      expect(service.restDue, isTrue);
      service.dispose();
    });

    test('剩余时间不会算成负数', () {
      final service = EyeCareService();
      service.tick(const Duration(minutes: 25));
      expect(service.remaining, Duration.zero);
      service.dispose();
    });

    test('倒计时 20 秒（20-20-20 法则）', () {
      expect(EyeCareService.restCountdownSeconds, 20);
    });
  });
}
