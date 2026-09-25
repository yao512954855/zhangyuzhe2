/// `core/util/format.dart` 的展示格式化 —— 重点盯 `formatRelativeDay` 的**时区口径**。
///
/// 为什么值得单独一组用例：服务端存的是 **UTC**（`...Z`），学生眼里的「今天」
/// 是**本地**日历日。两者在北京时间 00:00–08:00 之间**差一天** ——
/// 直接拿 UTC 日期去比，半夜背的书会被显示成「昨天」，
/// 而且是那种"看起来只是有点怪"的错，不炸、不报警。
/// （同一个坑在 `v_class_today_activity` 上踩过，见契约 §14.1 第 14 项。）
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/util/format.dart';

void main() {
  group('formatRelativeDay', () {
    /// 把「本地时间」反推成服务端会存的那种 UTC ISO 串。
    ///
    /// ⚠️ **不能直接写死 `'2026-09-25T10:00:00Z'` 再断言「今天」** ——
    /// 那样用例的期望值会跟着跑测机器的时区变，在 UTC 机器上必红。
    /// 从本地时间推 UTC 才既准确又与时区无关。
    String isoFromLocal(DateTime local) => local.toUtc().toIso8601String();

    test('同一天（本地凌晨也算今天）→ 今天', () {
      // 本地 02:00 → UTC 还是前一天 18:00（UTC+8 下）。
      // 如果实现漏了 `toLocal()`，这里会得到「昨天」。
      final iso = isoFromLocal(DateTime(2026, 9, 25, 2, 0));
      expect(formatRelativeDay(iso, now: DateTime(2026, 9, 25, 12, 0)), '今天');
    });

    test('本地昨天 23:30 → 昨天', () {
      final iso = isoFromLocal(DateTime(2026, 9, 24, 23, 30));
      expect(formatRelativeDay(iso, now: DateTime(2026, 9, 25, 1, 0)), '昨天');
    });

    test('3 天前 → 「3 天前」；7 天前 → 落到具体日期', () {
      final now = DateTime(2026, 9, 25, 12, 0);
      expect(
        formatRelativeDay(isoFromLocal(DateTime(2026, 9, 22, 9, 0)), now: now),
        '3 天前',
      );
      expect(
        formatRelativeDay(isoFromLocal(DateTime(2026, 9, 18, 9, 0)), now: now),
        '2026-09-18',
      );
    });

    test('未来时间（时钟漂移）按「今天」处理，不显示「-1 天前」', () {
      final iso = isoFromLocal(DateTime(2026, 9, 26, 8, 0));
      expect(formatRelativeDay(iso, now: DateTime(2026, 9, 25, 12, 0)), '今天');
    });

    test('空 / 脏数据 → 空串（调用方据此整段不显示，而不是显示 1970-01-01）', () {
      expect(formatRelativeDay(null), '');
      expect(formatRelativeDay(''), '');
      expect(formatRelativeDay('不是时间'), '');
      // ⚠️ 这一条是**真踩到的坑**：Dart 的 `DateTime.tryParse` 对越界字段是
      // **归一化而不是拒绝** —— `'0000-00-00'` 解析成 `-1-11-30`（月份 0
      // 回退成上一年 12 月），不抛异常也不为 null。幸好有年份闸兜住。
      expect(formatRelativeDay('0000-00-00'), '');
      expect(formatRelativeDay('9999-12-31T00:00:00Z'), '');
    });
  });

  group('既有格式化（回归护栏，改动不该波及）', () {
    test('formatDurationMs', () {
      expect(formatDurationMs(0), '0 秒');
      expect(formatDurationMs(45_000), '45 秒');
      expect(formatDurationMs(185_000), '3 分 05 秒');
    });

    test('formatPercent / formatScore', () {
      expect(formatPercent(0.856), '86%');
      expect(formatScore(88.5), '89'); // 四舍五入，UI 不显示小数
    });
  });
}
