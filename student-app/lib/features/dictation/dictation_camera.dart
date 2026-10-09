/// 听写拍照封装 —— image_picker 的薄接口层。
///
/// 与 `dictation_tts.dart` 同理：平台通道在 widget 测试里不可用，
/// 抽接口便于注入 fake（模拟「用户取消」/「拍到照片」）。
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';

/// 拍照能力接口。
abstract interface class DictationCamera {
  /// 拍一张手写答题照片；用户取消返回 null。
  Future<String?> takePhoto();
}

/// image_picker 默认实现。
class ImagePickerAdapter implements DictationCamera {
  final ImagePicker _picker = ImagePicker();

  @override
  Future<String?> takePhoto() async {
    final xfile = await _picker.pickImage(
      source: ImageSource.camera,
      maxWidth: 2048,
      imageQuality: 90,
    );
    return xfile?.path;
  }
}

/// 听写拍照提供者（测试 override 成 fake）。
final dictationCameraProvider = Provider<DictationCamera>(
  (ref) => ImagePickerAdapter(),
);
