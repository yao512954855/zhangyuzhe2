/// 听写播报页 —— WP-D D-2 第 2 项。
///
/// 逐题连播：报完一题等「播报间隔」再报下一题；进度 n/总；
/// 「重听」只重播当前题（**不计进度**）；「暂停/继续」；
/// 最后一题播完（或学生点「写完了」提前结束）自动进入拍照批改页，
/// 两条路都有明确推进，无静默卡死。
///
/// 红线：播报节奏与判分无关 —— 本页不上报任何 answer。
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme/age_profile.dart';
import '../../core/widgets/app_button.dart';
import 'dictation_capture_page.dart';
import 'dictation_models.dart';
import 'dictation_tts.dart';

/// 听写播报页。
class DictationPlayPage extends ConsumerStatefulWidget {
  const DictationPlayPage({
    super.key,
    required this.items,
    required this.settings,
    this.unitName = '',
    this.returnRoute,
    /// 测试注入 fake TTS；为空则从 provider 取默认实现。
    this.tts,
  });

  final List<DictationItem> items;
  final DictationSettings settings;
  final String unitName;
  final String? returnRoute;
  final DictationTts? tts;

  @override
  ConsumerState<DictationPlayPage> createState() => _DictationPlayPageState();
}

class _DictationPlayPageState extends ConsumerState<DictationPlayPage> {
  DictationTts? _providerTts;

  DictationTts get _tts {
    final injected = widget.tts;
    if (injected != null) {
      return injected; // 测试注入的 fake 由测试自己管理生命周期
    }
    _providerTts ??= ref.read(dictationTtsProvider);
    return _providerTts!;
  }

  int _index = 0;
  bool _paused = false;
  bool _finished = false;
  bool _speaking = false;
  Timer? _gapTimer;
  int _speakSeq = 0; // 作废在途回调：重听/暂停/退出后旧的推进不再生效

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        _speakCurrent();
      }
    });
  }

  @override
  void dispose() {
    _speakSeq++;
    _gapTimer?.cancel();
    _providerTts?.dispose(); // 注入的（fake）由测试自己管
    super.dispose();
  }

  DictationItem get _current => widget.items[_index];

  String get _currentText =>
      widget.settings.readChinese ? _current.zh : _current.en;

  /// 播当前题；播完后等间隔再推进。
  Future<void> _speakCurrent() async {
    _gapTimer?.cancel();
    final seq = ++_speakSeq;
    if (mounted) {
      setState(() => _speaking = true);
    }
    try {
      await _tts.setLanguage(widget.settings.ttsLanguage);
      await _tts.speak(_currentText);
    } catch (_) {
      // TTS 故障不静默卡死：给出提示，学生可点「重听当前题」重试。
      if (mounted && seq == _speakSeq) {
        ScaffoldMessenger.maybeOf(context)?.showSnackBar(
          const SnackBar(content: Text('朗读出了问题，可点「重听当前题」再试')),
        );
      }
    }
    if (!mounted || seq != _speakSeq || _paused || _finished) {
      return;
    }
    if (mounted) {
      setState(() => _speaking = false);
    }
    _gapTimer = Timer(Duration(seconds: widget.settings.intervalSeconds), _advance);
  }

  /// 推进到下一题；最后一题 → 进拍照批改页。
  void _advance() {
    if (!mounted || _finished) {
      return;
    }
    if (_index >= widget.items.length - 1) {
      _goCapture();
      return;
    }
    setState(() => _index += 1);
    _speakCurrent();
  }

  /// 结束播报，进拍照批改页（替换本页，返回键不再回到播报）。
  void _goCapture() {
    _speakSeq++;
    _gapTimer?.cancel();
    _tts.stop();
    if (!mounted) {
      return;
    }
    setState(() => _finished = true);
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => DictationCapturePage(
          items: widget.items,
          unitName: widget.unitName,
          returnRoute: widget.returnRoute,
        ),
      ),
    );
  }

  /// 重听当前题：只重播，**不计进度**（不动 _index）。
  void _replay() {
    if (_paused || _finished) {
      return;
    }
    _speakCurrent();
  }

  void _togglePause() {
    if (_finished) {
      return;
    }
    if (_paused) {
      setState(() => _paused = false);
      _speakCurrent(); // 从当前题重新播起（不跳题、不计进度）
    } else {
      _speakSeq++;
      _gapTimer?.cancel();
      _tts.stop();
      setState(() {
        _paused = true;
        _speaking = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final total = widget.items.length;
    return PopScope(
      canPop: true,
      child: Scaffold(
        appBar: AppBar(
          title: Text(widget.unitName.isEmpty ? '正在听写' : '听写 · ${widget.unitName}'),
        ),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
            child: Column(
              children: [
                Text('第 ${_index + 1} / $total 题', style: profile.pageTitleStyle),
                const SizedBox(height: AgeProfile.elementGap / 2),
                Text(
                  _paused ? '已暂停' : (_speaking ? '正在报，写在本子上…' : '想好了就写下来'),
                  style: profile.captionStyle,
                ),
                const Spacer(),
                Icon(
                  _paused ? Icons.pause_circle_outline_rounded : Icons.hearing_rounded,
                  size: 96,
                  color: profile.captionStyle.color,
                ),
                const Spacer(),
                Text('提示：纸上写好后点下面的按钮去拍照批改', style: profile.captionStyle),
                const SizedBox(height: AgeProfile.elementGap),
                Row(
                  children: [
                    Expanded(
                      child: AppButton(
                        label: '重听当前题',
                        kind: AppButtonKind.secondary,
                        onPressed: (_paused || _finished) ? null : _replay,
                      ),
                    ),
                    const SizedBox(width: AgeProfile.elementGap / 2),
                    Expanded(
                      child: AppButton(
                        label: _paused ? '继续' : '暂停',
                        kind: AppButtonKind.secondary,
                        onPressed: _finished ? null : _togglePause,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: AgeProfile.elementGap / 2),
                AppButton(
                  label: '写完了，去拍照批改',
                  onPressed: _finished ? null : _goCapture,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
