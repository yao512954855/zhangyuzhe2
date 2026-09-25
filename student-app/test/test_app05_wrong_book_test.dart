/// WP-APP-05 错题本 —— F1 / U1 + 语文 mode 红线。
///
/// 说明（重要）：本文件只做「生产代码层」断言，因为 `wrong_book_page.dart`
/// 复用 `practice_launcher` → `practice.dart` → 未落地的 `practice_page.dart`
/// (APP-03)，其编译错误会顺着依赖链污染本测试；"错题本页面渲染 / U1 单元排序
/// 展示 / U2 只显错词" 的 Widget 用例已就绪（见交付说明 D-APP03），待 APP-03
/// 落地后即可并入本文件。
///
/// 本文件覆盖：
/// - U1 / F1：只练错题复用生产组卷，`strategy.scope == "wrong"`；
/// - 红线：英语发 `mode`、语文**绝不**发 `mode`（`PracticeConfig.toRequest` 如实）；
/// - 契约缺口：一期只有英语错题（`PracticeScope.wrong` 仅在英语路径可用）。
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/features/config/practice_strategy.dart';
import 'package:student_app/features/home/models.dart';

void main() {
  group('U1 / F1 只练错题：生产组卷链路 scope=wrong', () {
    test('英语只练错题 → strategy.scope == "wrong" 且 content_type=english', () {
      final config = PracticeConfig(
        unit: const UnitItem(
          unitId: 11,
          subject: Subject.english,
          name: 'Unit 1 Hello!',
          wordTotal: 24,
          withError: 5,
        ),
        scope: PracticeScope.wrong,
      );
      final body = config.toRequest();
      final strategy = (body['strategy'] as Map).cast<String, dynamic>();
      expect(strategy['scope'], 'wrong', reason: '只练错题必须发 scope=wrong');
      expect(body['content_type'], 'english');
    });

    test('英语默认（整单元）也带 mode=cn2en，且 count_mode/count/order 字段齐全', () {
      final config = PracticeConfig(
        unit: const UnitItem(
          unitId: 11,
          subject: Subject.english,
          name: 'Unit 1 Hello!',
          wordTotal: 24,
        ),
        scope: PracticeScope.unit,
      );
      final body = config.toRequest();
      final strategy = (body['strategy'] as Map).cast<String, dynamic>();
      expect(strategy['scope'], 'unit');
      expect(strategy['mode'], 'cn2en', reason: '英语必须发 mode');
      expect(strategy['count_mode'], 'all');
      expect(strategy['order'], 'sequential');
    });
  });

  group('红线：语文请求体绝不发 mode', () {
    test('语文整单元 → strategy 不含 mode 键', () {
      final config = PracticeConfig(
        unit: const UnitItem(
          unitId: 12,
          subject: Subject.chinese,
          name: '第一单元',
          textTotal: 3,
        ),
        scope: PracticeScope.unit,
      );
      final body = config.toRequest();
      final strategy = (body['strategy'] as Map).cast<String, dynamic>();
      expect(strategy.containsKey('mode'), isFalse, reason: '语文请求体不得含 mode（否则被 Pydantic 拦成 10001）');
    });
  });

  group('契约缺口：一期只覆盖英语错题', () {
    test('英语可用 wrong 范围；语文 availableScopes 不含 wrong', () {
      final english = PracticeConfig(
        unit: const UnitItem(unitId: 11, subject: Subject.english, name: 'U', wordTotal: 24),
        scope: PracticeScope.unit,
      ).availableScopes;
      final chinese = PracticeConfig(
        unit: const UnitItem(unitId: 12, subject: Subject.chinese, name: 'C', textTotal: 3),
        scope: PracticeScope.unit,
      ).availableScopes;
      expect(english, contains(PracticeScope.wrong));
      expect(chinese, isNot(contains(PracticeScope.wrong)), reason: '语文一期无错题接口，不给 wrong 选项');
    });
  });
}
