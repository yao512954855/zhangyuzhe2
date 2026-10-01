/// WS 收帧探针 —— 用与 App **完全相同**的 `web_socket_channel` 3.0.3 连真后端，
/// 走 start → 推 PCM → end → ping，打印每一个收到的帧。
///
/// 目的：真机/App 卡「识别中」但服务端已发出 verdict（留痕日志证实），
/// 本探针用于切分「库收不到帧」还是「App 接线问题」。
///
/// 用法：`dart run tool/ws_probe.dart <args.json>`
/// args.json: {token, session_id, word_id, mode, pcm}
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:web_socket_channel/web_socket_channel.dart';

final Stopwatch _sw = Stopwatch()..start();

String _t() =>
    '${(_sw.elapsedMilliseconds / 1000).toStringAsFixed(1).padLeft(5)}s';

void main(List<String> args) async {
  final cfg =
      jsonDecode(File(args[0]).readAsStringSync()) as Map<String, dynamic>;
  final pcm = File(cfg['pcm'] as String).readAsBytesSync();
  final token = cfg['token'] as String;
  final sessionId = cfg['session_id'] as int;
  final wordId = cfg['word_id'] as int;
  final mode = cfg['mode'] as String;

  final uri = Uri.parse('ws://127.0.0.1:8810/ws/asr?token=$token');
  stdout.writeln('$_t 连接 $uri');
  final channel = WebSocketChannel.connect(uri);
  await channel.ready;
  stdout.writeln('$_t 已连接（ready 完成）');

  var gotVerdict = false;
  final sub = channel.stream.listen(
    (frame) {
      final s = frame.toString();
      stdout.writeln('$_t ← [${frame.runtimeType}] ${s.length > 220 ? s.substring(0, 220) : s}');
      if (s.contains('"type": "verdict"') || s.contains('"type":"verdict"')) {
        gotVerdict = true;
      }
    },
    onError: (Object e) => stdout.writeln('$_t !! 流错误: $e'),
    onDone: () => stdout.writeln('$_t !! 流关闭 closeCode=${channel.closeCode}'),
  );

  void sendJson(Map<String, dynamic> m) {
    channel.sink.add(jsonEncode(m));
    stdout.writeln('$_t → ${m['type']}');
  }

  sendJson({
    'type': 'start',
    'session_id': sessionId,
    'task_type': 'word',
    'word_id': wordId,
    'mode': mode,
    'retry_count': 0,
    'hint_level': 0,
    'used_hint_count': 0,
    'enable_intermediate': true,
  });

  for (var i = 0; i < pcm.length; i += 4800) {
    final end = (i + 4800) < pcm.length ? i + 4800 : pcm.length;
    channel.sink.add(pcm.sublist(i, end));
    await Future<void>.delayed(const Duration(milliseconds: 150));
  }
  stdout.writeln('$_t → 音频推完 ${pcm.length} 字节');
  sendJson({'type': 'end'});

  // 2 秒后补一个 ping，验证控制帧的入方向
  Timer(const Duration(seconds: 2), () => sendJson({'type': 'ping'}));

  // 最多等 30 秒
  final deadline = DateTime.now().add(const Duration(seconds: 30));
  while (DateTime.now().isBefore(deadline) && !gotVerdict) {
    await Future<void>.delayed(const Duration(milliseconds: 200));
  }

  await sub.cancel();
  await channel.sink.close();
  stdout.writeln(gotVerdict ? '$_t 结果：收到 verdict ✅' : '$_t 结果：30 秒内未收到 verdict ❌');
  exit(gotVerdict ? 0 : 1);
}
