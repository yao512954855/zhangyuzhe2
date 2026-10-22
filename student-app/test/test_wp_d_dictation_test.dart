/// WP-D 学生端听写链路 —— 页面可被直接驱动（入口接线是接力项 J1，归 WP-C）。
///
/// 覆盖（10-后端规格 WP-D 验收 + 30-学生端规格 D-2 + 契约 v1.3 APP-13 适配）：
/// - U4：播报页倒计时推进（v1.3：dictation_countdown_s，默认 3）、
///       可重听（不计进度）、可暂停/继续；
/// - U5：播完进入拍照页（无静默卡死）；
/// - U6：拍照页按钮态与错误提示（业务失败中文文案）；
/// - U7：结果页错题入本（mode=listen）；
/// - U8→F17-3：结果页**手动退出**（v1.3 移除自动跳回）；
/// - 设置页：报中文/报英文单选 + 间隔只读展示服务端值（v1.3：管理端可调）。
///
/// TTS / 相机全部注入 fake，无平台通道依赖。
/// v1.3 的 U17-1~U17-3 见 test_app13_dictation_test.dart。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/core/widgets/app_button.dart';
import 'package:student_app/features/dictation/dictation_capture_page.dart';
import 'package:student_app/features/dictation/dictation_camera.dart';
import 'package:student_app/features/dictation/dictation_models.dart';
import 'package:student_app/features/dictation/dictation_play_page.dart';
import 'package:student_app/features/dictation/dictation_result_page.dart';
import 'package:student_app/features/dictation/dictation_settings_page.dart';
import 'package:student_app/features/dictation/dictation_tts.dart';
import 'package:student_app/router/routes/dictation.dart';

const _items = [
  DictationItem(id: 1, en: 'apple', zh: '苹果'),
  DictationItem(id: 2, en: 'banana', zh: '香蕉'),
  DictationItem(id: 3, en: 'cat', zh: '猫'),
];

/// 可控的假 TTS：speak 立即完成（播报页据此时隔 interval 推进）。
class FakeTts implements DictationTts {
  final List<String> spoken = <String>[];
  final List<String> languages = <String>[];
  int stopCount = 0;

  @override
  Future<void> setLanguage(String language) async {
    languages.add(language);
  }

  @override
  Future<void> setSpeechRate(double rate) async {}

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

/// 假相机。
class FakeCamera implements DictationCamera {
  FakeCamera(this.path);

  final String? path;
  int calls = 0;

  @override
  Future<String?> takePhoto() async {
    calls++;
    return path;
  }
}

DictationSettings _settings({
  int interval = DictationSettings.defaultIntervalSeconds,
}) =>
    DictationSettings(readChinese: true, intervalSeconds: interval);

/// 页面依赖 AppExtraColors / AgeScope，测试统一套 day 主题 + 低年级档。
Widget _wrap(Widget home) => ProviderScope(
      // 设置页/播报页会尝试拉服务端设置：测试环境无后端 → 走失败回退（U17-1 口径）
      child: MaterialApp(
        theme: AppTheme.build(palette: AppPalettes.day),
        home: AgeScope(profile: AgeProfile.of(AgeBand.junior), child: home),
      ),
    );

/// 把结果页推到栈上，返回探针页 key（跳回后用它断言）。
Widget _hostPage(WidgetBuilder pushPage, {String probeKey = 'probe'}) =>
    Builder(
      builder: (context) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          Navigator.of(context).push(
            MaterialPageRoute<void>(builder: pushPage),
          );
        });
        return Scaffold(key: Key(probeKey), body: const SizedBox());
      },
    );

void main() {
  group('模型与设置约束', () {
    test('间隔步进夹在 5~30，默认 10', () {
      var s = const DictationSettings();
      expect(s.intervalSeconds, 10);
      for (var i = 0; i < 100; i++) {
        s = s.withIntervalDelta(1);
      }
      expect(s.intervalSeconds, DictationSettings.maxIntervalSeconds);
      for (var i = 0; i < 100; i++) {
        s = s.withIntervalDelta(-1);
      }
      expect(s.intervalSeconds, DictationSettings.minIntervalSeconds);
    });

    test('TTS 语言映射（契约 §8：报中文 zh-CN / 报英文 en-US）', () {
      expect(_settings().ttsLanguage, 'zh-CN');
      expect(_settings().copyWith(readChinese: false).ttsLanguage, 'en-US');
    });

    test('批改报告解析与对错计数', () {
      final report = DictationGradeReport.fromJson(const {
        'results': [
          {'word': 'apple', 'recognized': 'apple', 'correct': true, 'confidence': 99, 'uncertain': false},
          {'word': 'banana', 'recognized': 'bnana', 'correct': false, 'confidence': 92, 'uncertain': false},
          {'word': 'cat', 'recognized': 'cat', 'correct': true, 'confidence': 70, 'uncertain': true},
        ],
        'ocr_content': 'apple bnana cat',
      });
      expect(report.correctCount, 2);
      expect(report.wrongCount, 1);
      expect(report.results[2].uncertain, isTrue);
    });

    test('路由表四条齐全（J1 接线契约）', () {
      final routes = DictationRoutes.routes;
      expect(routes.keys, containsAll([
        DictationRoutes.settings,
        DictationRoutes.play,
        DictationRoutes.capture,
        DictationRoutes.result,
      ]));
    });
  });

  group('设置页', () {
    testWidgets('报中文/报英文单选 + 间隔只读展示（服务端值，测试环境拉取失败显示 --）；空题禁用开始', (tester) async {
      await tester.pumpWidget(
        _wrap(const DictationSettingsPage(items: [], unitName: 'Unit 1')),
      );
      // _loadRemoteCountdown 的 Future 异常兜底要跑完
      await tester.pumpAndSettle();
      expect(find.text('报中文'), findsOneWidget);
      expect(find.text('报英文'), findsOneWidget);
      // v1.3：间隔不可本地调（步进器已删），只读展示服务端值；拉取失败显示 --
      expect(find.text('隔多久报下一个'), findsOneWidget);
      expect(find.text('--'), findsOneWidget);
      expect(find.byIcon(Icons.add_circle_outline_rounded), findsNothing);
      expect(find.text('开始听写（共 0 题）'), findsOneWidget);
    });

    testWidgets('点开始 → 进入播报页，按当前设置播第一题', (tester) async {
      final tts = FakeTts();
      // 设置页跳播报页时播报页从 provider 取 TTS —— 这里 override 成 fake。
      await tester.pumpWidget(
        ProviderScope(
          overrides: [dictationTtsProvider.overrideWithValue(tts)],
          child: _wrap(
            DictationSettingsPage(
              items: _items,
              unitName: 'Unit 1',
              initialSettings: _settings(),
            ),
          ),
        ),
      );
      await tester.tap(find.text('开始听写（共 3 题）'));
      await tester.pump(); // push
      await tester.pump(); // postFrame → speak
      expect(find.text('第 1 / 3 题'), findsOneWidget);
      expect(tts.spoken.first, '苹果', reason: '报中文模式读 zh 字段');
      expect(tts.languages.last, 'zh-CN');
    });
  });

  group('播报页', () {
    testWidgets('U4：间隔推进 + 重听不计进度 + 暂停/继续', (tester) async {
      final tts = FakeTts();
      await tester.pumpWidget(
        _wrap(
          DictationPlayPage(
            items: _items,
            settings: _settings(interval: 10),
            tts: tts,
          ),
        ),
      );
      await tester.pump(); // postFrame speak
      expect(tts.spoken, ['苹果']);

      // 重听：再播同一题，进度仍 1/3
      await tester.tap(find.text('重听当前题'));
      await tester.pump();
      expect(tts.spoken, ['苹果', '苹果']);
      expect(find.text('第 1 / 3 题'), findsOneWidget);

      // 暂停：不再推进，状态提示
      await tester.tap(find.text('暂停'));
      await tester.pump();
      expect(find.text('已暂停'), findsOneWidget);
      final spokenAtPause = tts.spoken.length;

      // 暂停期间过了间隔也不推进
      await tester.pump(const Duration(seconds: 30));
      expect(find.text('第 1 / 3 题'), findsOneWidget);

      // 继续：从当前题重播
      await tester.tap(find.text('继续'));
      await tester.pump();
      expect(tts.spoken.length, greaterThan(spokenAtPause));

      // v1.3 F17-2：倒计时 3s（默认回退值）到点 → 进第 2 题
      await tester.pump(const Duration(seconds: 3));
      expect(find.text('第 2 / 3 题'), findsOneWidget);
      expect(tts.spoken.last, '香蕉');
    });

    testWidgets('U4b：报英文模式读 en（en-US）', (tester) async {
      final tts = FakeTts();
      await tester.pumpWidget(
        _wrap(
          DictationPlayPage(
            items: _items,
            settings:
                const DictationSettings(readChinese: false, intervalSeconds: 5),
            tts: tts,
          ),
        ),
      );
      await tester.pump();
      expect(tts.spoken.first, 'apple');
      expect(tts.languages.last, 'en-US');
      // 倒计时 3s 到点进第 2 题（v1.3 F17-2；pump 5s 覆盖窗口）
      await tester.pump(const Duration(seconds: 5));
      expect(find.text('第 2 / 3 题'), findsOneWidget);
    });

    testWidgets('U5：最后一题播完自动进拍照页（无静默卡死）', (tester) async {
      final tts = FakeTts();
      await tester.pumpWidget(
        _wrap(
          DictationPlayPage(
            items: _items,
            settings: _settings(interval: 5),
            tts: tts,
          ),
        ),
      );
      await tester.pump();
      // v1.3 F17-2：倒计时 3s 推进，三题走完 → 拍照页
      await tester.pump(const Duration(seconds: 5)); // → 第 2 题
      await tester.pump(const Duration(seconds: 5)); // → 第 3 题
      await tester.pump(const Duration(seconds: 5)); // 播完 → 拍照页
      await tester.pump();
      expect(
        find.text('把写好的答案拍下来'),
        findsOneWidget,
        reason: '播完必须落到拍照批改页',
      );
      expect(find.text('拍 照'), findsOneWidget);
    });

    testWidgets('U5b：提前结束入口 → 直接进拍照页', (tester) async {
      final tts = FakeTts();
      await tester.pumpWidget(
        _wrap(
          DictationPlayPage(
            items: _items,
            settings: _settings(interval: 10),
            tts: tts,
          ),
        ),
      );
      await tester.pump();
      await tester.tap(find.text('写完了，去拍照批改'));
      await tester.pump(); // pushReplacement 调度
      await tester.pump(); // 新路由首帧
      expect(find.text('把写好的答案拍下来'), findsOneWidget);
    });
  });

  group('拍照页', () {
    testWidgets('U6：未拍照时上传禁用；拍照取消保持占位', (tester) async {
      await tester.pumpWidget(
        _wrap(
          DictationCapturePage(items: _items, camera: FakeCamera(null)),
        ),
      );
      expect(
        tester.widget<AppButton>(find.byType(AppButton).last).onPressed,
        isNull,
        reason: '没照片不能上传',
      );

      await tester.tap(find.text('拍 照'));
      await tester.pump();
      expect(find.text('还没有照片'), findsOneWidget, reason: '取消拍照保持占位');
      expect(
        tester.widget<AppButton>(find.byType(AppButton).last).onPressed,
        isNull,
      );
    });

    testWidgets('U6b：拍到照片后可上传；上传失败给中文提示且按钮复位', (tester) async {
      // 注入 fake 上传：模拟后端业务失败（20005），避免真实 IO（FakeAsync 区会挂起）。
      await tester.pumpWidget(
        _wrap(
          DictationCapturePage(
            items: _items,
            camera: FakeCamera('/tmp/fake_photo.jpg'),
            uploader: (path, words) async {
              expect(path, '/tmp/fake_photo.jpg');
              expect(words, ['apple', 'banana', 'cat'],
                  reason: 'words 必须逐个对齐词表（契约 §4.1）');
              throw ApiException.business(20005, '批改暂时失败，请稍后再试');
            },
          ),
        ),
      );
      await tester.tap(find.text('拍 照'));
      await tester.pump();
      expect(find.text('重 拍'), findsOneWidget);
      expect(
        tester.widget<AppButton>(find.byType(AppButton).last).onPressed,
        isNotNull,
        reason: '有照片即可上传',
      );

      // 上传失败：业务异常的中文 message 上屏，finally 复位 _busy。
      await tester.tap(find.text('上传批改'));
      await tester.pump();
      await tester.pump();
      expect(find.text('批改暂时失败，请稍后再试'), findsOneWidget);
      expect(
        tester.widget<AppButton>(find.byType(AppButton).last).onPressed,
        isNotNull,
        reason: '失败后按钮复位可重试（_busy finally）',
      );
    });
  });

  group('结果页', () {
    final report = DictationGradeReport.fromJson(const {
      'results': [
        {'word': 'apple', 'recognized': 'apple', 'correct': true, 'confidence': 99, 'uncertain': false},
        {'word': 'banana', 'recognized': 'bnana', 'correct': false, 'confidence': 92, 'uncertain': false},
        {'word': 'cat', 'recognized': 'cat', 'correct': true, 'confidence': 70, 'uncertain': true},
      ],
      'ocr_content': 'apple bnana cat',
    });

    testWidgets('U7：对错清单 + 错题逐个入本（mode=listen）+ uncertain 标记', (tester) async {
      final added = <String>[];
      await tester.pumpWidget(
        _wrap(
          _hostPage(
            (_) => DictationResultPage(
              report: report,
              items: _items,
              wrongAdder: (itemId, mode) async {
                added.add('$itemId:$mode');
              },
            ),
          ),
        ),
      );
      await tester.pump(); // push
      await tester.pump(); // postFrame → auto-add
      await tester.pump();

      expect(find.text('对 2 · 错 1'), findsOneWidget);
      expect(find.text('认成：bnana'), findsOneWidget, reason: '错题显示实际识别值');
      expect(find.text('拿不准'), findsOneWidget, reason: '置信度 <85 标「拿不准」');
      expect(added, ['2:listen'], reason: '只有 banana 错，mode=listen');
    });

    testWidgets('U8→F17-3：结果页手动退出——等待期间无自动跳转，点「完成」才回栈底', (tester) async {
      await tester.pumpWidget(
        _wrap(
          _hostPage(
            (_) => DictationResultPage(
              report: report,
              items: _items,
              wrongAdder: (_, _) async {},
            ),
            probeKey: 'unit-list-probe',
          ),
        ),
      );
      await tester.pump(); // push
      await tester.pump(); // postFrame → auto-add
      await tester.pump();

      expect(find.byType(DictationResultPage), findsOneWidget);
      expect(find.text('完成'), findsOneWidget);

      // v1.3 F17-3：移除自动跳回 —— 等再久也不跳
      await tester.pump(const Duration(seconds: 5));
      expect(find.byType(DictationResultPage), findsOneWidget,
          reason: '结果页常驻，学生可以慢慢看（无自动退出）');

      // 点「完成」才退出（returnRoute 未配置 → 退回栈底探针页）
      await tester.tap(find.text('完成'));
      await tester.pump();
      await tester.pump();
      expect(find.byKey(const Key('unit-list-probe')), findsOneWidget,
          reason: '「完成」手动退出回单元列表');
      await tester.pumpAndSettle();
    });
  });
}
