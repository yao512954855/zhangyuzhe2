/// APP-04 单元详情「点课文直接开背」—— 2026-10-01 姚工反馈：
/// 想直接背第二课，得「设置练法 → 选课文 → 勾选 → 开始」点好几下，太绕。
///
/// 本文件钉住新交互的**请求契约边界**：
/// - 点课文行 = 按「选课文 + 只选这一篇」组卷（`scope=lesson` + `text_ids=[id]`），
///   与配置页手动勾一篇发出去的请求体**完全一致**，只是替学生把 5 步压成 1 步；
/// - 组好的卷要真的交到 `/recite`（背诵页），不是弹个"准备好啦"就完事；
/// - 语文不发 `mode`（契约 §9.1 英语专有，发错会被 Pydantic 拦成 10001）。
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
import 'package:student_app/features/config/practice_session.dart';
import 'package:student_app/features/home/home_controller.dart';
import 'package:student_app/features/home/models.dart';
import 'package:student_app/features/home/unit_detail_page.dart';
import 'package:student_app/router/routes/recite.dart';

import 'support/fake_home_repository.dart';

UnitItem chineseUnit() => const UnitItem(
      unitId: 12,
      subject: Subject.chinese,
      name: '第一单元',
      textTotal: 3,
      recited: 1,
      lastGrade: '良好',
      status: MasteryStatus.practicedOk,
    );

List<TextBrief> sampleTexts() => const [
      TextBrief(id: 301, unitId: 12, title: '背影', author: '朱自清', reciteScopeChars: 620),
      TextBrief(id: 302, unitId: 12, title: '春', author: '朱自清', reciteScopeChars: 300),
      TextBrief(id: 303, unitId: 12, title: '济南的冬天', author: '老舍', reciteScopeChars: 400),
    ];

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    SharedPreferences.setMockInitialValues({
      StoreKeys.serverHost: '127.0.0.1',
      StoreKeys.serverPort: 8810,
      StoreKeys.authToken: 'test-token',
    });
    local = await LocalStore.open();
    serverStore = ServerStore(local);
  });

  testWidgets('点课文行直接单篇开背：请求 scope=lesson 只选这一篇，卷交给 /recite',
      (tester) async {
    useTallScreen(tester);
    final adapter = JsonAdapter((options) {
      if (options.uri.path == '/api/sessions') {
        return okBody({
          'session_id': 9102,
          'unit_id': 12,
          'unit_name': '第一单元',
          'content_type': 'chinese',
          'items': [
            {
              'seq': 1,
              'text_id': 302,
              'mode': 'recite',
              'title': '春',
              'total_chars': 300,
              'recite_scope': [0],
            },
          ],
          'time_limit_ms': 0,
          'started_at': '2026-10-01T03:00:00Z',
        });
      }
      return okBody(const {});
    });

    PracticeSession? handed;
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          localStoreProvider.overrideWithValue(local),
          homeRepositoryProvider.overrideWithValue(
            FakeHomeRepository(texts: sampleTexts()),
          ),
          apiClientProvider.overrideWithValue(
            clientWith(adapter, serverStore: serverStore, localStore: local),
          ),
        ],
        child: MaterialApp(
          theme: AppTheme.build(palette: AppPalettes.day),
          // 背诵页用桩：本测试只验证「请求发对了 + 卷交给了背诵页」这层边界，
          // 背诵页自身由 test_app04_recite_test.dart 验。
          routes: {
            ReciteRoutes.recite: (context) {
              handed =
                  ModalRoute.of(context)?.settings.arguments as PracticeSession?;
              return const Scaffold(body: Text('背诵页桩'));
            },
          },
          home: AgeScope(
            profile: AgeProfile.of(AgeBand.junior),
            child: UnitDetailPage(unit: chineseUnit()),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    // 点第二课《春》的课文行。
    await tester.tap(find.text('春'));
    await tester.pumpAndSettle();

    expect(find.text('背诵页桩'), findsOneWidget, reason: '应当直接跳到背诵页');
    expect(handed, isNotNull, reason: '跳转必须把组好的卷作为路由参数传过去');
    expect(handed!.sessionId, 9102);
    expect(handed!.items.single.textId, 302);

    final request = adapter.received.firstWhere((r) => r.uri.path == '/api/sessions');
    final raw = request.data;
    final body = raw is String
        ? jsonDecode(raw) as Map<String, dynamic>
        : (raw as Map).cast<String, dynamic>();
    expect(body['unit_id'], 12);
    expect(body['content_type'], 'chinese');

    final strategy = (body['strategy'] as Map).cast<String, dynamic>();
    expect(strategy['scope'], 'lesson');
    expect(strategy['scope_detail']['text_ids'], [302],
        reason: '只勾点进去的这一篇，不是全选');
    expect(strategy.containsKey('mode'), isFalse, reason: '语文不发 mode（契约 §9.1）');
  });

  testWidgets('课文行带「点我能背」的可见反馈（麦克风图标），不再只是死卡片', (tester) async {
    useTallScreen(tester);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          localStoreProvider.overrideWithValue(local),
          homeRepositoryProvider.overrideWithValue(
            FakeHomeRepository(texts: sampleTexts()),
          ),
        ],
        child: MaterialApp(
          theme: AppTheme.build(palette: AppPalettes.day),
          home: AgeScope(
            profile: AgeProfile.of(AgeBand.junior),
            child: UnitDetailPage(unit: chineseUnit()),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    // 每行都有一个麦克风图标（与 MasteryBadge 并排的可见 affordance）。
    expect(
      find.byIcon(Icons.mic_rounded),
      findsNWidgets(3),
      reason: '三篇课文各一个「点我能背」图标',
    );
    // 操作指引也要在（学生第一次进来得知道行可以点）。
    expect(find.textContaining('点任意课文直接开背'), findsOneWidget);
  });
}
