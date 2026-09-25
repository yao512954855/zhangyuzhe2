/// 年级学期选择 —— F2 / F3（UI 规范 §6.1 用眼提示的同类"强制引导一次"）。
///
/// **两种进入方式，同一个页面**：
/// - `force: true`：首次登录且偏好为空 —— 由 `home_page` 直接整页渲染，
///   **不提供返回**（没有上一步可回）。这样"强制"是靠结构保证的，
///   而不是靠弹窗拦返回键 —— 后者在安卓实体返回键上很容易被绕过。
/// - `force: false`：学生点首页顶部胶囊进来改 —— 有返回，选完 `pop` 回首页。
///
/// 年级用九宫格、学期用两枚胶囊，**一律卡片式**（不用下拉框）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import 'choice_card.dart';
import 'home_controller.dart';
import 'time_format.dart';

class GradeSemesterPickerPage extends ConsumerStatefulWidget {
  const GradeSemesterPickerPage({super.key, this.force = false, this.clock});

  /// 首次引导（不可返回）。
  final bool force;

  /// 可注入的当前时间 —— 学期预选依赖月份，注入后单测不受运行日期影响。
  final DateTime? clock;

  @override
  ConsumerState<GradeSemesterPickerPage> createState() => _GradeSemesterPickerPageState();
}

class _GradeSemesterPickerPageState extends ConsumerState<GradeSemesterPickerPage> {
  int? _grade;
  late int _semester;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    final current = ref.read(gradePrefProvider);
    _grade = current.grade;
    // 学期按当前月份预选（减少一次点击）；年级必须学生自己选
    _semester = current.semester ?? suggestSemester(widget.clock ?? DateTime.now());
  }

  Future<void> _submit() async {
    final grade = _grade;
    if (grade == null || _saving) {
      return;
    }
    setState(() => _saving = true);
    try {
      // 本地先行 + 服务端补同步 + 首页刷新，全在 controller 里收口
      await ref.read(homeControllerProvider.notifier).applyGradeSemester(
            grade: grade,
            semester: _semester,
          );
    } finally {
      if (mounted) {
        setState(() => _saving = false);
      }
    }
    // force 模式不 pop：年级一存好，首页就会自动渲染出真内容（见 home_page）
    if (!widget.force && mounted && Navigator.of(context).canPop()) {
      Navigator.of(context).pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(
        title: Text(widget.force ? '先选一下年级' : '切换年级学期'),
        automaticallyImplyLeading: !widget.force,
      ),
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) {
            const gap = 10.0;
            final cellWidth = (constraints.maxWidth - AgeProfile.screenEdgeInset * 2 - gap * 2) / 3;
            return ListView(
              padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
              children: [
                if (widget.force) ...[
                  Text(
                    '选好之后，练的内容就是你这个年级的了',
                    style: profile.bodyStyle.copyWith(color: extra.textSub),
                  ),
                  const SizedBox(height: AgeProfile.elementGap),
                ],
                ChoiceSection(
                  title: '你是几年级的？',
                  child: Wrap(
                    spacing: gap,
                    runSpacing: gap,
                    children: [
                      for (var grade = 1; grade <= 9; grade++)
                        SizedBox(
                          width: cellWidth,
                          child: ChoiceCard(
                            label: '$grade 年级',
                            selected: _grade == grade,
                            expand: true,
                            onTap: () => setState(() => _grade = grade),
                          ),
                        ),
                    ],
                  ),
                ),
                const SizedBox(height: AgeProfile.elementGap * 1.5),
                ChoiceSection(
                  title: '现在是哪个学期？',
                  child: ChoiceGroup<int>(
                    options: const [1, 2],
                    selected: _semester,
                    labelOf: semesterLabel,
                    onSelect: (value) => setState(() => _semester = value),
                  ),
                ),
                const SizedBox(height: AgeProfile.elementGap * 2),
                AppButton(
                  label: '好了，开始学',
                  loading: _saving,
                  onPressed: _grade == null ? null : _submit,
                ),
                const SizedBox(height: 8),
                Center(
                  child: Text(
                    '选错了也没关系，随时能在首页上面改',
                    style: profile.captionStyle.copyWith(color: extra.textFaint),
                  ),
                ),
              ],
            );
          },
        ),
      ),
    );
  }
}
