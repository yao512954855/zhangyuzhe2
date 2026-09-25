/// 保存 / 套用「我的练习方案」 —— WP-2H（二期 §15.2 端点 `/api/me/practice-plans`）。
///
/// 设计要点：
/// - **复用现有配置对象 [PracticeConfig]**，不重写配置模型（契约 §6 独占文件约束）。
///   序列化 / 套用逻辑用本文件的**扩展方法 + 纯函数**完成，不碰
///   `practice_strategy.dart` / `practice_config_controller.dart`。
/// - `payload` 是**配置维度的忠实快照**：语文如实**不发 `mode`**（契约口径：
///   语文没有 mode 维度）、英语 `scope=lesson/paragraph` 静默退化等由配置对象本身决定，
///   这里只原样记录，不伪造不支持的维度。
/// - 套用时调用 `PracticeConfigController` 已有的公开 setter（`setScope` / `setTextSelection` /
///   `setRange` / `setCountMode` / `setCount` / `setOrder` / `setMode` / `setTimeLimit`），
///   逐个回填，**不新增配置模型的写入方法**。
library;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../home/models.dart';
import 'practice_config_controller.dart';
import 'practice_strategy.dart';

// ===========================================================================
// 数据模型
// ===========================================================================

/// 一条已保存的练习方案（服务端 JSON → 对象）。
@immutable
class PracticePlan {
  const PracticePlan({
    required this.id,
    required this.name,
    required this.subject,
    required this.payload,
    this.createdAt,
    this.updatedAt,
  });

  factory PracticePlan.fromJson(Map<String, dynamic> json) => PracticePlan(
        id: _asInt(json['id']) ?? 0,
        name: json['name']?.toString() ?? '',
        subject: Subject.tryParse(json['subject']) ?? Subject.english,
        payload: json['payload'] is Map
            ? Map<String, dynamic>.from(json['payload'] as Map)
            : const <String, dynamic>{},
        createdAt: json['created_at']?.toString(),
        updatedAt: json['updated_at']?.toString(),
      );

  final int id;
  final String name;
  final Subject subject;

  /// 配置维度快照（scope / count_mode / order / mode / time_limit / …）。
  final Map<String, dynamic> payload;

  final String? createdAt;
  final String? updatedAt;
}

// ===========================================================================
// 配置对象 ↔ 方案快照（扩展方法，不修改配置模型文件）
// ===========================================================================

/// 把当前 [PracticeConfig] 序列化成方案 payload（忠实、JSON 友好）。
extension PlanPayload on PracticeConfig {
  Map<String, dynamic> toPlanPayload() {
    final p = <String, dynamic>{
      'scope': scope.wire,
      'count_mode': countMode.wire,
      'count': count,
      'order': order.wire,
      'time_limit': {
        'kind': timeLimit.kind.name,
        'seconds': timeLimit.seconds,
      },
    };
    if (scope == PracticeScope.lesson) {
      p['selected_text_ids'] = selectedTextIds.toList()..sort();
    }
    if (scope == PracticeScope.range) {
      p['range_start'] = rangeStart;
      p['range_end'] = rangeEnd;
    }
    // 语文如实**不发 mode**（契约口径：语文没有 mode 维度）；英语发真实 mode。
    if (isEnglish) {
      p['mode'] = mode.wire;
    }
    return p;
  }
}

/// 把方案 payload 套回到配置控制器（逐维度调用既有 setter，不重写模型）。
///
/// 仅当 `unitSubject == english` 时才回填 `mode`：英语专属维度不该落到语文配置上。
void applyPlanPayloadToConfig({
  required PracticeConfigController ctrl,
  required PracticeConfig current,
  required Subject unitSubject,
  required Map<String, dynamic> payload,
}) {
  final scope = PracticeScope.values.firstWhere(
    (s) => s.wire == payload['scope']?.toString(),
    orElse: () => current.scope,
  );
  ctrl.setScope(scope);

  if (scope == PracticeScope.lesson) {
    final ids = <int>{};
    final raw = payload['selected_text_ids'];
    if (raw is List) {
      for (final e in raw) {
        final id = int.tryParse('$e');
        if (id != null) ids.add(id);
      }
    }
    ctrl.setTextSelection(ids);
  }
  if (scope == PracticeScope.range) {
    final start = _asInt(payload['range_start']);
    final end = _asInt(payload['range_end']);
    if (start != null || end != null) {
      ctrl.setRange(start: start, end: end);
    }
  }

  final countMode = CountMode.values.firstWhere(
    (m) => m.wire == payload['count_mode']?.toString(),
    orElse: () => current.countMode,
  );
  ctrl.setCountMode(countMode);
  final count = _asInt(payload['count']);
  if (count != null) ctrl.setCount(count);

  final order = OrderMode.values.firstWhere(
    (o) => o.wire == payload['order']?.toString(),
    orElse: () => current.order,
  );
  ctrl.setOrder(order);

  // mode 仅英语有意义
  if (unitSubject == Subject.english) {
    final modeWire = payload['mode']?.toString();
    if (modeWire != null) {
      final mode = EnglishMode.values.firstWhere(
        (m) => m.wire == modeWire,
        orElse: () => current.mode,
      );
      ctrl.setMode(mode);
    }
  }

  final tl = payload['time_limit'];
  if (tl is Map) {
    final kindName = tl['kind']?.toString();
    final seconds = _asInt(tl['seconds']) ?? 0;
    final kind = TimeLimitKind.values.firstWhere(
      (k) => k.name == kindName,
      orElse: () => TimeLimitKind.none,
    );
    ctrl.setTimeLimit(TimeLimit(kind, seconds));
  }
}

// ===========================================================================
// 仓库（只换路径，解包/判 code 仍走生产 ApiClient）
// ===========================================================================

/// 「我的练习方案」网络仓库。
class PlanRepository {
  PlanRepository({required this.api});

  final ApiClient api;

  Future<List<PracticePlan>> list() async {
    final data = await api.get<Map<String, dynamic>>('/api/me/practice-plans');
    final items = data?['items'];
    if (items is! List) return const [];
    return [
      for (final raw in items)
        if (raw is Map)
          PracticePlan.fromJson(Map<String, dynamic>.from(raw)),
    ];
  }

  Future<int> save({
    required String name,
    required Subject subject,
    required Map<String, dynamic> payload,
  }) async {
    final data = await api.post<Map<String, dynamic>>(
      '/api/me/practice-plans',
      body: {
        'name': name,
        'subject': subject.wire,
        'payload': payload,
      },
    );
    final id = data?['id'];
    if (id is int) return id;
    if (id is num) return id.toInt();
    throw ApiException.parse();
  }

  Future<void> update({
    required int id,
    String? name,
    Subject? subject,
    Map<String, dynamic>? payload,
  }) async {
    final body = <String, dynamic>{};
    if (name != null) body['name'] = name;
    if (subject != null) body['subject'] = subject.wire;
    if (payload != null) body['payload'] = payload;
    await api.put('/api/me/practice-plans/$id', body: body);
  }

  Future<void> delete(int id) async {
    await api.delete('/api/me/practice-plans/$id');
  }
}

final planRepositoryProvider = Provider<PlanRepository>(
  (ref) => PlanRepository(api: ref.watch(apiClientProvider)),
);

// ===========================================================================
// 控制器（列表 / 保存 / 删除的状态）
// ===========================================================================

@immutable
class PlanSaveState {
  const PlanSaveState({
    this.plans = const [],
    this.loading = false,
    this.saving = false,
    this.errorText,
  });

  final List<PracticePlan> plans;
  final bool loading;
  final bool saving;
  final String? errorText;

  PlanSaveState copyWith({
    List<PracticePlan>? plans,
    bool? loading,
    bool? saving,
    String? errorText,
    bool clearError = false,
  }) =>
      PlanSaveState(
        plans: plans ?? this.plans,
        loading: loading ?? this.loading,
        saving: saving ?? this.saving,
        errorText: clearError ? null : (errorText ?? this.errorText),
      );
}

class PlanSaveController extends Notifier<PlanSaveState> {
  @override
  PlanSaveState build() => const PlanSaveState();

  Future<void> load() async {
    state = state.copyWith(loading: true, clearError: true);
    try {
      final plans = await ref.read(planRepositoryProvider).list();
      state = state.copyWith(plans: plans, loading: false);
    } on ApiException catch (e) {
      state = state.copyWith(loading: false, errorText: e.displayText);
    }
  }

  Future<int?> save({
    required String name,
    required Subject subject,
    required Map<String, dynamic> payload,
  }) async {
    state = state.copyWith(saving: true, clearError: true);
    try {
      final id = await ref.read(planRepositoryProvider).save(
            name: name,
            subject: subject,
            payload: payload,
          );
      await load();
      state = state.copyWith(saving: false);
      return id;
    } on ApiException catch (e) {
      state = state.copyWith(saving: false, errorText: e.displayText);
      return null;
    }
  }

  Future<void> delete(int id) async {
    state = state.copyWith(clearError: true);
    try {
      await ref.read(planRepositoryProvider).delete(id);
      state = state.copyWith(
        plans: state.plans.where((p) => p.id != id).toList(),
      );
    } on ApiException catch (e) {
      state = state.copyWith(errorText: e.displayText);
    }
  }
}

final planSaveProvider =
    NotifierProvider<PlanSaveController, PlanSaveState>(PlanSaveController.new);

// ===========================================================================
// UI：保存 / 套用 底部面板
// ===========================================================================

/// 「我的练习方案」面板：保存当前配置为一个命名方案，或从已有方案里套用 / 删除。
///
/// 通过 [unit] 拿到当前学科（套用时用于约束 `mode` 这类英语专属维度）。
/// [unit] 可为空：从「我的」页进入时未必带着单元上下文，此时学科降级取当前
/// 已配置练习的学科（[practiceConfigProvider] 里的 `config.unit`）；两者皆无时
/// 保存/套用会被面板自身的 `config == null` 守卫拦下，不会写出错数据。
class PlanSaveSheet extends ConsumerStatefulWidget {
  const PlanSaveSheet({this.unit, super.key});

  final UnitItem? unit;

  @override
  ConsumerState<PlanSaveSheet> createState() => _PlanSaveSheetState();
}

class _PlanSaveSheetState extends ConsumerState<PlanSaveSheet> {
  final TextEditingController _nameCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    // 放到首帧之后：本 widget 在 build 里 `ref.watch(planSaveProvider)`，
    // 首帧内同步写该 provider 会撞上"构建期间修改状态"的断言（与配置页同款处理）。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(planSaveProvider.notifier).load();
    });
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  void _onSave() async {
    final name = _nameCtrl.text.trim();
    final config = ref.read(practiceConfigProvider).config;
    if (name.isEmpty || config == null) return;
    final subject = widget.unit?.subject ?? config.unit.subject;
    final id = await ref.read(planSaveProvider.notifier).save(
          name: name,
          subject: subject,
          payload: config.toPlanPayload(),
        );
    if (id != null && mounted) {
      _nameCtrl.clear();
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('已保存方案')),
      );
    }
  }

  void _onApply(PracticePlan plan) {
    final ctrl = ref.read(practiceConfigProvider.notifier);
    final current = ref.read(practiceConfigProvider).config;
    if (current == null) return;
    applyPlanPayloadToConfig(
      ctrl: ctrl,
      current: current,
      unitSubject: widget.unit?.subject ?? current.unit.subject,
      payload: plan.payload,
    );
    if (mounted && Navigator.of(context).canPop()) {
      Navigator.of(context).pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final planState = ref.watch(planSaveProvider);
    final config = ref.watch(practiceConfigProvider).config;
    final canSave = config != null && !planState.saving;

    return Padding(
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 16,
        bottom: MediaQuery.of(context).viewInsets.bottom + 16,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('我的练习方案',
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 12),
          // ---- 保存当前配置 ----
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _nameCtrl,
                  decoration: const InputDecoration(
                    labelText: '方案名称',
                    hintText: '例如：考前冲刺',
                  ),
                ),
              ),
              const SizedBox(width: 8),
              ElevatedButton(
                onPressed: canSave ? _onSave : null,
                child: planState.saving
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('保存'),
              ),
            ],
          ),
          if (config == null)
            const Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text('请先在配置页选好练习方式再保存',
                  style: TextStyle(color: Colors.grey)),
            ),
          if (planState.errorText != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(planState.errorText!,
                  style: const TextStyle(color: Colors.red)),
            ),
          const SizedBox(height: 12),
          const Divider(),
          // ---- 已有方案列表 ----
          if (planState.loading)
            const Center(child: CircularProgressIndicator())
          else if (planState.plans.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 16),
              child: Text('还没有保存过的方案',
                  style: TextStyle(color: Colors.grey)),
            )
          else
            Flexible(
              child: ListView.separated(
                shrinkWrap: true,
                itemCount: planState.plans.length,
                separatorBuilder: (context, index) => const Divider(height: 1),
                itemBuilder: (context, i) {
                  final plan = planState.plans[i];
                  return ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(plan.name),
                    subtitle: Text(plan.subject.label),
                    trailing: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        TextButton(
                          onPressed: () => _onApply(plan),
                          child: const Text('套用'),
                        ),
                        IconButton(
                          icon: const Icon(Icons.delete_outline),
                          tooltip: '删除',
                          onPressed: () =>
                              ref.read(planSaveProvider.notifier).delete(plan.id),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// 内部工具
// ---------------------------------------------------------------------------

int? _asInt(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return int.tryParse(value?.toString() ?? '');
}
