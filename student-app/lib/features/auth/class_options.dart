/// 注册页可选的班级 —— 契约 §4.1 补充 `GET /api/classes/options`。
///
/// **这条接口是免鉴权的**：注册发生在登录之前，学生打开注册页时手里没有任何
/// token。所以别把它换成带鉴权的路径（例如管理端的 `GET /api/classes`）——
/// 那样注册页永远拉不到班级，UI 规范 §7.3「班级选择用卡片列表」就只能永远
/// 显示「先不选」。
///
/// **拉不到不阻断注册**：`class_id` 在契约 §2.2 本就是可选字段。失败时这里
/// 返回空列表，界面退回「先不选」，学生照样能注册，之后由管理者归班。
library;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';

/// 一个可选班级（契约 §4.1 补充：只有 `id` / `name` / `grade` / `semester`）。
@immutable
class ClassOption {
  const ClassOption({
    required this.id,
    required this.name,
    required this.grade,
    required this.semester,
  });

  factory ClassOption.fromJson(Map<String, dynamic> json) => ClassOption(
        id: (json['id'] as num?)?.toInt() ?? 0,
        name: json['name']?.toString() ?? '',
        grade: (json['grade'] as num?)?.toInt() ?? 0,
        semester: json['semester']?.toString() ?? '',
      );

  final int id;
  final String name;
  final int grade;

  /// `first` / `second`（契约 §1.1）。
  final String semester;

  /// 卡片副标题：年级 + 学期。同名学生（不同年级）靠它区分。
  String get subtitle {
    final semesterText = switch (semester) {
      'first' => '上学期',
      'second' => '下学期',
      _ => '',
    };
    if (grade <= 0) return semesterText;
    return semesterText.isEmpty ? '$grade 年级' : '$grade 年级 · $semesterText';
  }
}

/// 拉取注册页的班级清单（免鉴权接口，见文件头）。
///
/// 失败（网络 / 服务端异常）**不抛给界面**：返回空列表，注册页只显示「先不选」。
final classOptionsProvider = FutureProvider<List<ClassOption>>((ref) async {
  final api = ref.watch(apiClientProvider);
  try {
    final data = await api.get<Map<String, dynamic>>('/api/classes/options');
    final items = data?['items'];
    if (items is! List) return const <ClassOption>[];
    return items
        .whereType<Map>()
        .map((e) => ClassOption.fromJson(Map<String, dynamic>.from(e)))
        .toList(growable: false);
  } on ApiException {
    return const <ClassOption>[];
  }
});
