/// 方案 B 对齐纯函数回归（test_spell_align_test.dart）。
///
/// `alignSpellCells`（spell_page.dart）：把「听到的字母」对齐到「目标字母」，
/// 判错后逐格着色的依据 —— 对=match / 错=wrong / 缺=missing / 多读=尾部 wrong。
/// 用例直接取自 2026-10-16 真机实测的识别序列（banana/howareyou 等翻车现场）。
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/features/recite/spell_page.dart';

void main() {
  // 私有类型不具名：用 status.toString()（'_CellStatus.match'）取尾段断言
  List<String> statuses(List<Object> cells) => [
        for (final c in cells)
          (c as dynamic).status.toString().split('.').last as String,
      ];
  List<String?> letters(List<Object> cells) =>
      [for (final c in cells) (c as dynamic).heard as String?];

  group('alignSpellCells（方案 B 逐格对齐）', () {
    test('全对：apple 每格 match', () {
      final cells = alignSpellCells(
        ['A', 'P', 'P', 'L', 'E'],
        ['A', 'P', 'P', 'L', 'E'],
      );
      expect(statuses(cells), everyElement('match'));
      expect(letters(cells), ['A', 'P', 'P', 'L', 'E']);
    });

    test('替换：applz vs apple → 4 match + 尾格 wrong(Z)', () {
      final cells = alignSpellCells(
        ['A', 'P', 'P', 'L', 'E'],
        ['A', 'P', 'P', 'L', 'Z'],
      );
      expect(statuses(cells),
          ['match', 'match', 'match', 'match', 'wrong']);
      expect(letters(cells), ['A', 'P', 'P', 'L', 'Z']);
    });

    test('缺读：baaa vs banana（真机实测 n 全丢）→ 缺格红空框', () {
      final cells = alignSpellCells(
        ['B', 'A', 'N', 'A', 'N', 'A'],
        ['B', 'A', 'A', 'A'],
      );
      // 代价最优解：B/A match，A→N 替换，A→A match，末尾 N/A missing
      // （或等价缺读形态——断言只锁「结果集」：无 missing 漏标 + 代价守恒）
      final st = statuses(cells);
      expect(st, hasLength(6));
      expect(st.where((s) => s == 'missing').length, greaterThanOrEqualTo(1));
      expect(st.where((s) => s == 'match').length, greaterThanOrEqualTo(3));
      // 听到的 4 个字母都要出现在格子里
      final shown = letters(cells).whereType<String>().toList();
      expect(shown, hasLength(4));
      expect(shown.join(), contains('B'));
    });

    test('多读：bananas vs banana → 6 match + 尾部 1 个 extra(S)', () {
      final cells = alignSpellCells(
        ['B', 'A', 'N', 'A', 'N', 'A'],
        ['B', 'A', 'N', 'A', 'N', 'A', 'S'],
      );
      expect(statuses(cells), [
        'match', 'match', 'match', 'match', 'match', 'match', 'wrong',
      ]);
      expect(letters(cells)?.last, 'S');
    });

    test('近似：howaruyou vs howareyou（真机 e→u）→ 8 match + 1 wrong', () {
      final cells = alignSpellCells(
        ['H', 'O', 'W', 'A', 'R', 'E', 'Y', 'O', 'U'],
        ['H', 'O', 'W', 'A', 'R', 'U', 'Y', 'O', 'U'],
      );
      expect(statuses(cells).where((s) => s == 'match').length, 8);
      expect(statuses(cells).where((s) => s == 'wrong').length, 1);
    });

    test('全缺：heard 空 → 目标每格 missing', () {
      final cells = alignSpellCells(['C', 'A', 'T'], []);
      expect(statuses(cells), ['missing', 'missing', 'missing']);
      expect(letters(cells), [null, null, null]);
    });

    test('幻觉串：abcdefg vs cat → 不崩，代价守恒（3 格 + 4 extra）', () {
      final cells = alignSpellCells(
        ['C', 'A', 'T'],
        ['A', 'B', 'C', 'D', 'E', 'F', 'G'],
      );
      expect(cells, hasLength(7)); // 3 目标格 + 4 extra
      // 顺序相反（target C..A，heard A..C）只能对上 1 个字母
      expect(statuses(cells).where((s) => s == 'match').length, 1);
    });
  });
}
