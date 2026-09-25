/// WP-2H 保存 / 套用「我的练习方案」 —— `PlanSaveSheet` 组件测试。
///
/// 钉住几件容易假绿的事：
/// 1. **请求打到正确端点与方法**：保存 = `POST /api/me/practice-plans`，
///    列表 = `GET /api/me/practice-plans`，删除 = `DELETE /api/me/practice-plans/{id}`。
/// 2. **payload 忠实**：保存时带的 payload 与当前 `PracticeConfig` 一致；
///    语文如实**不发 mode**（本测试用英语单元，断言 payload 含 `mode`，
///    反向用例见后端 `test_chinese_payload_without_mode`）。
/// 3. **套用真的改配置**：点「套用」后 `practiceConfigProvider` 的 scope 被改。
/// 4. **只回自己的 / 删除生效**：列表渲染、`DELETE` 被调用、条目从列表消失。

library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/features/config/plan_save.dart';
import 'package:student_app/features/config/practice_config_controller.dart';
import 'package:student_app/features/config/practice_strategy.dart';
import 'package:student_app/features/home/models.dart';

import 'support/fake_home_repository.dart';
import 'test_app05_helpers.dart';

/// 一个方案 JSON。
Map<String, dynamic> planJson({
  required int id,
  required String name,
  required String subject,
  required Map<String, dynamic> payload,
}) =>
    {
      'id': id,
      'name': name,
      'subject': subject,
      'payload': payload,
      'created_at': '2026-09-25T09:00:00Z',
      'updated_at': null,
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    (local, serverStore) = await openTestStore();
  });

  /// 适配 `/api/me/practice-plans` 的假件。
  JsonAdapter planAdapter({
    required Map<String, dynamic> listBody,
    int failTimes = 0,
    ApiException? failWith,
  }) {
    var listCalls = 0;
    return JsonAdapter((options) {
      final path = options.uri.path;
      final method = options.method.toUpperCase();
      if (path == '/api/me/practice-plans') {
        if (method == 'GET') {
          listCalls++;
          if (listCalls <= failTimes && failWith != null) {
            return '{"code":${failWith.code},"message":"${failWith.message}",'
                '"data":null,"trace_id":"t"}';
          }
          return okBody(listBody);
        }
        if (method == 'POST') {
          return okBody({'id': 555});
        }
        if (method == 'DELETE') {
          return okBody(const {});
        }
      }
      // 其余 path 默认空体，避免误命中
      return okBody(const {});
    });
  }

  /// 包一个能抓 ProviderContainer 的最小 App（以便读取 `practiceConfigProvider` 状态）。
  ///
  /// 关键修复：面板在真实 app 里是经 `showModalBottomSheet` 弹出的——它本身自带
  /// `Material` 祖先，所以 `TextField` 不会报 "No Material widget found"。
  /// 测试之前直接把它挂到 `MaterialApp(home:)` 下，缺 Material 祖先才崩。
  /// 这里改用同款 `showModalBottomSheet` 挂载，与生产路径一致。
  Future<ProviderContainer> boot(
    WidgetTester tester,
    UnitItem unit,
    JsonAdapter adapter,
  ) async {
    useTallScreen(tester);
    final containerHolder = <ProviderContainer?>[null];
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          localStoreProvider.overrideWithValue(local),
          apiClientProvider.overrideWithValue(
            clientWith(adapter, serverStore: serverStore, localStore: local),
          ),
        ],
        child: MaterialApp(
          theme: AppTheme.build(palette: AppPalettes.day),
          home: AgeScope(
            profile: AgeProfile.of(AgeBand.junior),
            child: Builder(
              builder: (context) {
                containerHolder[0] = ProviderScope.containerOf(context);
                return Scaffold(body: _Opener(unit: unit));
              },
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    return containerHolder[0]!;
  }

  group('列表渲染', () {
    testWidgets('打开即拉 GET，方案名称上屏', (tester) async {
      final adapter = planAdapter(
        listBody: {
          'items': [
            planJson(
              id: 1,
              name: '周末复习',
              subject: 'english',
              payload: {'scope': 'unit'},
            ),
            planJson(
              id: 2,
              name: '考前冲刺',
              subject: 'chinese',
              payload: {'scope': 'lesson', 'selected_text_ids': [12]},
            ),
          ],
        },
      );
      await boot(tester, englishUnit(), adapter);

      // GET 打到正确端点
      expect(
        adapter.received.any((r) => r.uri.path == '/api/me/practice-plans'),
        isTrue,
      );
      // 方案名称渲染
      expect(find.text('周末复习'), findsOneWidget);
      expect(find.text('考前冲刺'), findsOneWidget);
      // 学科标签（中文映射）
      expect(find.text('语文'), findsOneWidget);
    });

    testWidgets('空列表给出明确提示', (tester) async {
      final adapter = planAdapter(listBody: {'items': []});
      await boot(tester, englishUnit(), adapter);
      expect(find.text('还没有保存过的方案'), findsOneWidget);
    });
  });

  group('保存', () {
    testWidgets('输入名称并保存 → POST 带正确的 name/subject/payload', (tester) async {
      final adapter = planAdapter(listBody: {'items': []});
      await boot(tester, englishUnit(), adapter);

      await tester.enterText(find.byType(TextField), '我的方案一');
      await tester.tap(find.widgetWithText(ElevatedButton, '保存'));
      await tester.pumpAndSettle();

      final post = adapter.received.firstWhere(
        (r) => r.uri.path == '/api/me/practice-plans' && r.method.toUpperCase() == 'POST',
      );
      final raw = post.data;
      final body = raw is String
          ? (jsonDecode(raw) as Map<String, dynamic>)
          : Map<String, dynamic>.from(raw as Map);

      expect(body['name'], '我的方案一');
      expect(body['subject'], 'english');
      final payload = (body['payload'] as Map).cast<String, dynamic>();
      // 默认英语配置：整单元 / 顺序 / cn2en / 不限时
      expect(payload['scope'], 'unit');
      expect(payload['count_mode'], 'all');
      expect(payload['order'], 'sequential');
      expect(payload['mode'], 'cn2en');
      expect((payload['time_limit'] as Map)['kind'], 'none');
    });
  });

  group('套用', () {
    testWidgets('点「套用」把 payload 的 scope 套回配置', (tester) async {
      final adapter = planAdapter(
        listBody: {
          'items': [
            planJson(
              id: 42,
              name: '只练没掌握的',
              subject: 'english',
              payload: {
                'scope': 'unmastered',
                'count_mode': 'all',
                'order': 'sequential',
                'mode': 'cn2en',
                'time_limit': {'kind': 'none', 'seconds': 0},
              },
            ),
          ],
        },
      );
      final container = await boot(tester, englishUnit(), adapter);

      // 套用前 scope 是默认 unit
      expect(container.read(practiceConfigProvider).config?.scope,
          PracticeScope.unit);

      await tester.tap(find.widgetWithText(TextButton, '套用'));
      await tester.pumpAndSettle();

      // 套用后 scope 被改
      expect(container.read(practiceConfigProvider).config?.scope,
          PracticeScope.unmastered);
    });
  });

  group('删除', () {
    testWidgets('点删除 → DELETE 被调用且条目从列表消失', (tester) async {
      final adapter = planAdapter(
        listBody: {
          'items': [
            planJson(
              id: 77,
              name: '要删掉的',
              subject: 'english',
              payload: {'scope': 'unit'},
            ),
          ],
        },
      );
      await boot(tester, englishUnit(), adapter);

      expect(find.text('要删掉的'), findsOneWidget);

      await tester.tap(find.byIcon(Icons.delete_outline));
      await tester.pumpAndSettle();

      // DELETE 打到正确端点且带 id
      final del = adapter.received.firstWhere(
        (r) =>
            r.uri.path == '/api/me/practice-plans/77' &&
            r.method.toUpperCase() == 'DELETE',
      );
      expect(del, isNotNull);

      // 条目从列表消失
      expect(find.text('要删掉的'), findsNothing);
    });
  });
}

/// 模拟配置页「我的方案」入口：先准备好配置，再以 `showModalBottomSheet` 弹出面板。
///
/// 配置初始化放在 `addPostFrameCallback` 里——直接写在 `initState` 会同步写
/// `practiceConfigProvider`，撞上"构建期间修改 provider"断言。这与面板自身
/// `load()` 的处理方式一致。
class _Opener extends ConsumerStatefulWidget {
  const _Opener({required this.unit});
  final UnitItem unit;

  @override
  ConsumerState<_Opener> createState() => _OpenerState();
}

class _OpenerState extends ConsumerState<_Opener> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      // 等价于从配置页进入：先把当前单元的配置准备好，才能保存/套用。
      await ref.read(practiceConfigProvider.notifier).start(widget.unit);
      if (!mounted) return;
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        builder: (_) => PlanSaveSheet(unit: widget.unit),
      );
    });
  }

  @override
  Widget build(BuildContext context) => const SizedBox.shrink();
}

UnitItem englishUnit() => UnitItem(
      unitId: 11,
      subject: Subject.english,
      name: 'Unit 1 Hello!',
      wordTotal: 24,
      practiced: 20,
      mastered: 14,
      accuracy: 0.88,
      status: MasteryStatus.practicedWithError,
    );
