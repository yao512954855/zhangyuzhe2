/// APP-13 听写增强验收 —— U17-1 / U17-2 / U17-3（52-二轮学生端工作包规格）。
///
/// - U17-1 报词显示：dictation_hint=zh 屏显中文、=en 屏显英文；
///   拉取失败回退 zh 不炸；
/// - U17-2 倒计时：N 秒到点自动报下一个，报词序列不重不漏；
/// - U17-3 结果页手动退出：等待期间无自动跳转，点「完成」才退出；
/// - settings 拉取：`GET /api/settings` 成功应用 / 失败回退（契约 §4.1）。
///
/// TTS 全部注入 fake；settings 拉取走假 adapter（不碰真网络）。
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/features/dictation/dictation_models.dart';
import 'package:student_app/features/dictation/dictation_play_page.dart';
import 'package:student_app/features/dictation/dictation_result_page.dart';
import 'package:student_app/features/dictation/dictation_tts.dart';

import 'support/fake_home_repository.dart';

const _items = [
  DictationItem(id: 1, en: 'apple', zh: '苹果'),
  DictationItem(id: 2, en: 'banana', zh: '香蕉'),
  DictationItem(id: 3, en: 'cat', zh: '猫'),
];

/// 可控的假 TTS：speak 立即完成（倒计时从报词后起算）。
class FakeTts implements DictationTts {
  final List<String> spoken = <String>[];
  final List<String> languages = <String>[];

  @override
  Future<void> setLanguage(String language) async {
    languages.add(language);
  }

  @override
  Future<void> speak(String text) async {
    spoken.add(text);
  }

  @override
  Future<void> stop() async {}

  @override
  Future<void> dispose() async {}
}

Widget _wrap(Widget home) => MaterialApp(
      theme: AppTheme.build(palette: AppPalettes.day),
      home: AgeScope(profile: AgeProfile.of(AgeBand.junior), child: home),
    );

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
  group('U17-1 报词显示（dictation_hint，报词动画区下方）', () {
    testWidgets('hint=zh → 报 apple 时屏显「苹果」', (tester) async {
      await tester.pumpWidget(
        _wrap(
          DictationPlayPage(
            items: _items,
            settings: const DictationSettings(),
            tts: FakeTts(),
            remoteSettings:
                const DictationRemoteSettings(hint: 'zh', countdownSeconds: 3),
          ),
        ),
      );
      await tester.pump(); // postFrame → speak + 屏显
      expect(find.text('苹果'), findsOneWidget);
      expect(find.text('apple'), findsNothing);
    });

    testWidgets('hint=en → 屏显「apple」；倒计时文案在报词动画区下方', (tester) async {
      await tester.pumpWidget(
        _wrap(
          DictationPlayPage(
            items: _items,
            settings: const DictationSettings(),
            tts: FakeTts(),
            remoteSettings:
                const DictationRemoteSettings(hint: 'en', countdownSeconds: 3),
          ),
        ),
      );
      await tester.pump();
      expect(find.text('apple'), findsOneWidget);
      expect(find.text('苹果'), findsNothing);
      expect(find.text('3 秒后报下一个'), findsOneWidget);
    });

    testWidgets('拉取失败回退 zh / 3，不炸', (tester) async {
      // 不注入 remoteSettings、不给 ProviderScope → /api/settings 拉取必失败。
      await tester.pumpWidget(
        _wrap(
          DictationPlayPage(
            items: _items,
            settings: const DictationSettings(),
            tts: FakeTts(),
          ),
        ),
      );
      await tester.pump();
      await tester.pump();
      expect(find.text('苹果'), findsOneWidget, reason: '失败回退 zh');
      expect(find.text('3 秒后报下一个'), findsOneWidget, reason: '倒计时回退 3');
    });

    testWidgets('进听写拉 GET /api/settings：dictation_hint=en、countdown=5 生效',
        (tester) async {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues(<String, Object>{
        StoreKeys.serverHost: '127.0.0.1',
        StoreKeys.serverPort: 8810,
      });
      final local = await LocalStore.open();
      final serverStore = ServerStore(local);
      final adapter = JsonAdapter((options) {
        if (options.uri.path == '/api/settings') {
          return okBody(<String, dynamic>{
            'dictation_countdown_s': 5,
            'spell_verdict_countdown_s': 3,
            'dictation_hint': 'en',
          });
        }
        return okBody(null);
      });
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            localStoreProvider.overrideWithValue(local),
            apiClientProvider.overrideWithValue(
              clientWith(adapter, serverStore: serverStore, localStore: local),
            ),
          ],
          child: _wrap(
            DictationPlayPage(
              items: _items,
              settings: const DictationSettings(),
              tts: FakeTts(),
            ),
          ),
        ),
      );
      await tester.pump(); // postFrame → speak + 发起拉取
      // 100ms：足够排空 dio 的 0 时长定时链路，但不触发 1s 倒计时跳。
      await tester.pump(const Duration(milliseconds: 100));
      await tester.pump(); // 拉取返回 → setState 重建
      expect(find.text('apple'), findsOneWidget, reason: 'dictation_hint=en');
      expect(find.text('5 秒后报下一个'), findsOneWidget,
          reason: 'dictation_countdown_s=5（从新值完整重数）');
    });

    testWidgets('settings 业务失败（code!=0）→ 回退 zh / 3 不炸', (tester) async {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues(<String, Object>{
        StoreKeys.serverHost: '127.0.0.1',
        StoreKeys.serverPort: 8810,
      });
      final local = await LocalStore.open();
      final serverStore = ServerStore(local);
      final adapter = JsonAdapter((options) => jsonEncode(<String, dynamic>{
            'code': 500,
            'message': '服务器开小差了',
            'data': null,
          }));
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            localStoreProvider.overrideWithValue(local),
            apiClientProvider.overrideWithValue(
              clientWith(adapter, serverStore: serverStore, localStore: local),
            ),
          ],
          child: _wrap(
            DictationPlayPage(
              items: _items,
              settings: const DictationSettings(),
              tts: FakeTts(),
            ),
          ),
        ),
      );
      await tester.pump(); // postFrame → speak + 发起拉取
      await tester.pump(const Duration(milliseconds: 100)); // 排空 dio 链路
      await tester.pump();
      expect(find.text('苹果'), findsOneWidget, reason: '业务失败回退 zh');
      expect(find.text('apple'), findsNothing);
      expect(find.text('3 秒后报下一个'), findsOneWidget, reason: '倒计时回退 3');
    });
  });

  group('U17-2 倒计时（dictation_countdown_s，到点自动报下一个）', () {
    testWidgets('N=2：逐秒倒数，到点推进；报词序列不重不漏', (tester) async {
      final tts = FakeTts();
      await tester.pumpWidget(
        _wrap(
          DictationPlayPage(
            items: _items,
            settings: const DictationSettings(),
            tts: tts,
            remoteSettings:
                const DictationRemoteSettings(hint: 'zh', countdownSeconds: 2),
          ),
        ),
      );
      await tester.pump(); // 报第 1 题
      expect(tts.spoken, ['苹果']);
      expect(find.text('2 秒后报下一个'), findsOneWidget);

      await tester.pump(const Duration(seconds: 1));
      expect(find.text('1 秒后报下一个'), findsOneWidget);
      expect(find.text('第 1 / 3 题'), findsOneWidget, reason: '未到点不推进');

      await tester.pump(const Duration(seconds: 1));
      expect(find.text('第 2 / 3 题'), findsOneWidget);
      expect(tts.spoken, ['苹果', '香蕉'], reason: '到点自动报下一个');

      await tester.pump(const Duration(seconds: 2));
      expect(find.text('第 3 / 3 题'), findsOneWidget);
      expect(tts.spoken, ['苹果', '香蕉', '猫'], reason: '报词序列不重不漏');

      await tester.pump(const Duration(seconds: 2)); // 最后一题到点 → 拍照页
      await tester.pump();
      expect(find.text('把写好的答案拍下来'), findsOneWidget);
    });
  });

  group('U17-3 结果页手动退出', () {
    final report = DictationGradeReport.fromJson(const {
      'results': [
        {'word': 'apple', 'recognized': 'apple', 'correct': true, 'confidence': 99, 'uncertain': false},
        {'word': 'banana', 'recognized': 'bnana', 'correct': false, 'confidence': 92, 'uncertain': false},
      ],
      'ocr_content': 'apple bnana',
    });

    testWidgets('批改完成展示结果 + 「完成」按钮；等待期间无自动跳转；点击才退出',
        (tester) async {
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
            probeKey: 'unit-list-probe',
          ),
        ),
      );
      await tester.pump(); // push
      await tester.pump(); // postFrame → auto-add
      await tester.pump();

      expect(find.text('对 1 · 错 1'), findsOneWidget);
      expect(find.text('完成'), findsOneWidget);
      expect(added, ['2:listen'], reason: '错题入本照常（mode=listen）');

      await tester.pump(const Duration(seconds: 10));
      expect(find.byKey(const Key('unit-list-probe')), findsNothing,
          reason: '等待期间无自动跳转（F17-3 移除自动退出）');

      await tester.tap(find.text('完成'));
      await tester.pump();
      await tester.pump();
      expect(find.byKey(const Key('unit-list-probe')), findsOneWidget,
          reason: '点「完成」手动退出');
      await tester.pumpAndSettle();
    });
  });
}
