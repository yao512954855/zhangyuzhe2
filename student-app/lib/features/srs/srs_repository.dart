/// SRS 间隔重复 —— 仓库层（WP-2F，二期）。
///
/// 只封装两个端点（契约 §15.2），统一走生产 `ApiClient`：
/// - `GET  /api/me/srs/due`
/// - `POST /api/me/srs/review`
///
/// 所有响应由 `ApiClient` 解包 + 判 `code`；业务失败抛中文 [ApiException]，
/// 调用方（页面）直接上屏 `e.displayText`，不重复处理。
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import 'srs_models.dart';

/// SRS 仓库。
class SrsRepository {
  SrsRepository({required this.api});

  final ApiClient api;

  /// 拉取当前学生的到期复习列表。
  Future<SrsDuePage> fetchDue({int limit = 20}) async {
    final data = await api.get<Map<String, dynamic>>(
      '/api/me/srs/due',
      query: {'limit': limit},
    );
    return SrsDuePage.fromJson(data ?? const <String, dynamic>{});
  }

  /// 复习一条计划，按 SM-2 推进（grade 为 0–5 的质量分）。
  Future<SrsReviewResult> review({required int wordId, required int grade}) async {
    final data = await api.post<Map<String, dynamic>>(
      '/api/me/srs/review',
      body: {'word_id': wordId, 'grade': grade},
    );
    return SrsReviewResult.fromJson(data ?? const <String, dynamic>{});
  }
}

/// 仓库 provider（依赖注入）。
final srsRepositoryProvider = Provider<SrsRepository>(
  (ref) => SrsRepository(api: ref.watch(apiClientProvider)),
);

/// 到期列表 provider（页面 watch；复习成功后 invalidate 重新拉取）。
///
/// 同 [myHomeworkProvider]：不启用 Riverpod 自动重试，重试交回 [SrsPage] 的
/// 「再试一次」按钮显式触发，避免静默重试掩盖业务错误。
final srsDueProvider = FutureProvider<SrsDuePage>(
  (ref) => ref.watch(srsRepositoryProvider).fetchDue(),
  retry: (_, _) => null,
);
