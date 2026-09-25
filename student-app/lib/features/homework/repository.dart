/// 作业仓库层 —— WP-2G（二期契约 §15.2：`GET /api/me/homework`）。
///
/// 只封装这一个端点，统一走生产 `ApiClient`：解包 + 判 `code`，业务失败抛中文
/// [ApiException]，页面直接上屏 `e.displayText`，不重复处理。
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import 'models.dart';

/// 作业仓库。
class HomeworkRepository {
  HomeworkRepository({required this.api});

  final ApiClient api;

  /// 拉取当前学生的作业列表（只看自己班级且 active 的）。
  Future<List<HomeworkItem>> list() async {
    final data = await api.get<Map<String, dynamic>>('/api/me/homework');
    final items = data?['items'];
    return parseHomeworkItems(items);
  }
}

/// 仓库 provider（依赖注入）。
final homeworkRepositoryProvider = Provider<HomeworkRepository>(
  (ref) => HomeworkRepository(api: ref.watch(apiClientProvider)),
);

/// 作业列表 provider（页面 watch；写作业后 invalidate 重新拉取）。
///
/// **不启用 Riverpod 3.x 的自动重试**：本页错误态由 [ErrorView] 提供「再试一次」
/// 按钮（点按即 `ref.invalidate`），后台静默重试会与这个显式重试控件打架、还会对
/// 业务错误（如登录过期 10002）无意义地重复请求。重试交回用户主动触发。
final myHomeworkProvider = FutureProvider<List<HomeworkItem>>(
  (ref) => ref.watch(homeworkRepositoryProvider).list(),
  retry: (_, _) => null,
);
