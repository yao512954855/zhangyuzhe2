/// 听写拍照批改页 —— WP-D D-2 第 3 项。
///
/// image_picker 拍照 → 预览 → 上传 `POST /api/dictation/grade`
/// （multipart `file` + `words` **逐个** form 字段，契约 §4.1）。
/// 批改成功 → 进结果页；业务失败（20005/20006）→ 中文提示留在本页重试。
library;

import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import 'dictation_camera.dart';
import 'dictation_models.dart';
import 'dictation_result_page.dart';

/// 上传批改的可替换实现：照片路径 + 词表 → 接口 data。
/// 默认实现组 multipart（file + words 逐个 form 字段）走 ApiClient。
typedef DictationUploader = Future<Map<String, dynamic>?> Function(
  String photoPath,
  List<String> words,
);

/// 听写拍照批改页。
class DictationCapturePage extends ConsumerStatefulWidget {
  const DictationCapturePage({
    super.key,
    required this.items,
    this.unitName = '',
    this.returnRoute,
    /// 测试注入 fake 相机；为空则从 provider 取默认实现。
    this.camera,
    /// 测试注入 fake 上传；为空则走默认 multipart 实现。
    this.uploader,
  });

  final List<DictationItem> items;
  final String unitName;
  final String? returnRoute;

  /// 测试注入。
  final DictationCamera? camera;

  /// 测试注入。
  final DictationUploader? uploader;

  @override
  ConsumerState<DictationCapturePage> createState() =>
      _DictationCapturePageState();
}

class _DictationCapturePageState extends ConsumerState<DictationCapturePage> {
  DictationCamera? _camera;
  String? _photoPath;
  bool _busy = false;
  String? _errorMessage;

  DictationCamera get _cameraOrProvider {
    _camera ??= widget.camera ?? ref.read(dictationCameraProvider);
    return _camera!;
  }

  Future<void> _takePhoto() async {
    if (_busy) {
      return;
    }
    setState(() => _errorMessage = null);
    try {
      final path = await _cameraOrProvider.takePhoto();
      if (!mounted) {
        return;
      }
      setState(() => _photoPath = path); // null = 用户取消，保持原状即可
    } catch (_) {
      if (mounted) {
        setState(() => _errorMessage = '拍照没有成功，请再试一次');
      }
    }
  }

  Future<void> _upload() async {
    final path = _photoPath;
    if (path == null || _busy || !mounted) {
      return;
    }
    setState(() {
      _busy = true;
      _errorMessage = null;
    });
    try {
      final uploader = widget.uploader ?? _defaultUpload;
      // 拍照批改比普通请求慢（后端 OCR 超时 15s），放宽客户端超时。
      final data = await uploader(
        path,
        widget.items.map((e) => e.en).toList(growable: false),
      );
      if (!mounted) {
        return;
      }
      final report = DictationGradeReport.fromJson(data ?? const {});
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => DictationResultPage(
            report: report,
            items: widget.items,
            unitName: widget.unitName,
            returnRoute: widget.returnRoute,
          ),
        ),
      );
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _errorMessage = e.displayText);
      }
    } catch (_) {
      if (mounted) {
        setState(() => _errorMessage = '批改没有成功，请稍后再试');
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  /// 默认上传实现：multipart `file` + `words` 逐个 form 字段（契约 §4.1）。
  Future<Map<String, dynamic>?> _defaultUpload(
    String photoPath,
    List<String> words,
  ) async {
    final form = FormData();
    form.files.add(
      MapEntry(
        'file',
        await MultipartFile.fromFile(photoPath, filename: 'dictation.jpg'),
      ),
    );
    for (final word in words) {
      form.fields.add(MapEntry('words', word));
    }
    return ref
        .read(apiClientProvider)
        .post<Map<String, dynamic>>(
          '/api/dictation/grade',
          body: form,
          override: const RequestOptionsOverride(
            timeout: Duration(seconds: 30),
          ),
        );
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final photoPath = _photoPath;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.unitName.isEmpty ? '拍照批改' : '拍照批改 · ${widget.unitName}'),
      ),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: ListView(
                padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
                children: [
                  Text('把写好的答案拍下来', style: profile.pageTitleStyle),
                  const SizedBox(height: AgeProfile.elementGap / 2),
                  Text(
                    '光线亮一点、字都拍进画面里，批改会更准',
                    style: profile.captionStyle,
                  ),
                  const SizedBox(height: AgeProfile.elementGap),
                  if (_errorMessage != null) ...[
                    InlineNotice(text: _errorMessage!, kind: NoticeKind.error),
                    const SizedBox(height: AgeProfile.elementGap),
                  ],
                  _PhotoPreview(path: photoPath),
                  const SizedBox(height: AgeProfile.elementGap),
                  AppButton(
                    label: photoPath == null ? '拍 照' : '重 拍',
                    kind: AppButtonKind.secondary,
                    onPressed: _busy ? null : _takePhoto,
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AgeProfile.screenEdgeInset, 0,
                AgeProfile.screenEdgeInset, AgeProfile.elementGap,
              ),
              child: AppButton(
                label: _busy ? '正在批改…' : '上传批改',
                loading: _busy,
                onPressed: photoPath == null || _busy ? null : _upload,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 照片预览（未拍时显示占位说明）。
class _PhotoPreview extends StatelessWidget {
  const _PhotoPreview({required this.path});

  final String? path;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final path = this.path;
    if (path == null) {
      return _previewBox(
        context,
        Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.photo_camera_rounded, size: 48),
              const SizedBox(height: 8),
              Text('还没有照片', style: profile.captionStyle),
            ],
          ),
        ),
      );
    }
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: SizedBox(
        height: 240,
        width: double.infinity,
        child: Image.file(
          File(path),
          fit: BoxFit.cover,
          // 预览高度固定：不随屏宽撑到 4:3 那么高，保证「拍 照」按钮
          // 在常规屏幕上不滚出可视区（拍照→重拍要一眼可达）。
          errorBuilder: (_, _, _) => _previewBox(
            context,
            Center(child: Text('照片读不出来，请重拍', style: profile.captionStyle)),
          ),
        ),
      ),
    );
  }

  Widget _previewBox(BuildContext context, Widget child) {
    return Container(
      height: 240,
      width: double.infinity,
      decoration: BoxDecoration(
        color: Theme.of(context).cardColor,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(width: 1),
      ),
      clipBehavior: Clip.antiAlias,
      child: child,
    );
  }
}
