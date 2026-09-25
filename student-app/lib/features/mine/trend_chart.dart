/// 个人正确率趋势图 —— WP-2A（二期，恢复 Wave 1 摘除的趋势图组件）。
///
/// 数据源：`GET /api/me/accuracy-trend`（`{points:[{date,accuracy,answered,correct}]}`）。
/// 正确率由后端按「剔除 empty 的有效题数」口径算好（见 `trend_service.accuracy_trend`），
/// 客户端**只做展示，不重算**。
///
/// ⚠️ **不引入第三方图表库**：pubspec 不在本包可改范围内，故用 [CustomPainter] 自绘。
/// ⚠️ **护眼硬约束**：纯静态绘制，不做任何闪烁 / 呼吸动画（避免 >3Hz 频闪）。
library;

import 'package:flutter/material.dart';

import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';

/// 单日趋势点（契约 §15.5 形状）。
@immutable
class TrendPoint {
  const TrendPoint({
    required this.date,
    this.accuracy = 0,
    this.answered = 0,
    this.correct = 0,
  });

  factory TrendPoint.fromJson(Map<String, dynamic> json) => TrendPoint(
        date: json['date']?.toString() ?? '',
        accuracy: _toDouble(json['accuracy']),
        answered: _toInt(json['answered']),
        correct: _toInt(json['correct']),
      );

  /// `YYYY-MM-DD`。
  final String date;

  /// 当天正确率（0–1）。
  final double accuracy;

  /// 当天答题条数（含 empty）。
  final int answered;

  /// 当天正确条数。
  final int correct;

  /// 仅取 `MM-DD`，给横轴标签用。
  String get shortDate {
    final parts = date.split('-');
    if (parts.length == 3) {
      return '${parts[1]}-${parts[2]}';
    }
    return date;
  }
}

/// `GET /api/me/accuracy-trend` 的完整响应。
@immutable
class AccuracyTrend {
  const AccuracyTrend({this.points = const []});

  factory AccuracyTrend.fromJson(Map<String, dynamic> json) {
    final raw = json['points'];
    final points = <TrendPoint>[];
    if (raw is List) {
      for (final e in raw) {
        if (e is Map) {
          points.add(TrendPoint.fromJson(Map<String, dynamic>.from(e)));
        }
      }
    }
    return AccuracyTrend(points: points);
  }

  final List<TrendPoint> points;

  /// 最近一天的正确率（无数据时为 0）。
  double get latestAccuracy =>
      points.isNotEmpty ? points.last.accuracy : 0;
}

/// 正确率趋势折线图。
///
/// - 无数据：居中提示「最近还没有练习记录」（不画空占位线）；
/// - 有数据：[CustomPainter] 画折线 + 数据点 + 网格 + 轴标签，纯静态。
class TrendChart extends StatelessWidget {
  const TrendChart({super.key, required this.points, this.height = 200});

  final List<TrendPoint> points;
  final double height;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    if (points.isEmpty) {
      return SizedBox(
        height: height,
        child: Center(
          child: Text(
            '最近还没有练习记录',
            style: profile.captionStyle.copyWith(color: extra.textSub),
          ),
        ),
      );
    }

    return SizedBox(
      height: height,
      child: CustomPaint(
        size: Size.infinite,
        painter: _TrendPainter(
          points: points,
          lineColor: extra.hint,
          gridColor: extra.divider,
          labelColor: extra.textSub,
          accentColor: extra.success,
        ),
      ),
    );
  }
}

/// 趋势折线图的画笔。所有几何都按传入尺寸算，不依赖设备像素比之外的隐式状态。
class _TrendPainter extends CustomPainter {
  _TrendPainter({
    required this.points,
    required this.lineColor,
    required this.gridColor,
    required this.labelColor,
    required this.accentColor,
  });

  final List<TrendPoint> points;
  final Color lineColor;
  final Color gridColor;
  final Color labelColor;
  final Color accentColor;

  static const double _paddingLeft = 36;
  static const double _paddingRight = 12;
  static const double _paddingTop = 12;
  static const double _paddingBottom = 24;
  static const List<double> _gridRatios = [0, 0.25, 0.5, 0.75, 1];

  @override
  void paint(Canvas canvas, Size size) {
    final plotLeft = _paddingLeft;
    final plotRight = size.width - _paddingRight;
    final plotTop = _paddingTop;
    final plotBottom = size.height - _paddingBottom;
    final plotW = plotRight - plotLeft;
    final plotH = plotBottom - plotTop;

    // ---- 网格 + 左侧 Y 轴标签（0% / 25% / ... / 100%）----
    final gridPaint = Paint()..color = gridColor..strokeWidth = 1;
    for (final ratio in _gridRatios) {
      final y = plotBottom - ratio * plotH;
      canvas.drawLine(Offset(plotLeft, y), Offset(plotRight, y), gridPaint);
      _drawLabel(
        canvas,
        '${(ratio * 100).toInt()}%',
        Offset(plotLeft - 6, y),
        align: TextAlign.right,
      );
    }

    final n = points.length;
    double xAt(int i) =>
        n == 1 ? plotLeft + plotW / 2 : plotLeft + plotW * i / (n - 1);
    double yAt(double acc) => plotBottom - acc.clamp(0.0, 1.0) * plotH;

    // ---- 折线与数据点 ----
    final linePaint = Paint()
      ..color = lineColor
      ..strokeWidth = 2.5
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;

    if (n > 1) {
      final path = Path();
      for (var i = 0; i < n; i++) {
        final x = xAt(i);
        final y = yAt(points[i].accuracy);
        if (i == 0) {
          path.moveTo(x, y);
        } else {
          path.lineTo(x, y);
        }
      }
      canvas.drawPath(path, linePaint);
    }

    final dotFill = Paint()..color = lineColor;
    final dotStroke = Paint()..color = Colors.white..strokeWidth = 2;
    for (var i = 0; i < n; i++) {
      final x = xAt(i);
      final y = yAt(points[i].accuracy);
      canvas.drawCircle(Offset(x, y), 3.5, dotFill);
      canvas.drawCircle(Offset(x, y), 3.5, dotStroke);
    }

    // ---- 横轴日期标签：首 / 中 / 尾，避免 30 天挤成一团 ----
    if (n > 0) {
      final ticks = <int>{0, (n - 1) ~/ 2, n - 1}.where((i) => i >= 0 && i < n);
      for (final i in ticks) {
        _drawLabel(
          canvas,
          points[i].shortDate,
          Offset(xAt(i), plotBottom + 6),
          align: TextAlign.center,
        );
      }
    }
  }

  void _drawLabel(Canvas canvas, String text, Offset base, {TextAlign align = TextAlign.left}) {
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(color: labelColor, fontSize: 11),
      ),
      textDirection: TextDirection.ltr,
      textAlign: align,
    );
    tp.layout();
    var dx = base.dx;
    if (align == TextAlign.center) {
      dx -= tp.width / 2;
    } else if (align == TextAlign.right) {
      dx -= tp.width;
    }
    tp.paint(canvas, Offset(dx, base.dy - tp.height / 2));
  }

  @override
  bool shouldRepaint(covariant _TrendPainter old) =>
      old.points != points ||
      old.lineColor != lineColor ||
      old.gridColor != gridColor ||
      old.labelColor != labelColor ||
      old.accentColor != accentColor;
}

// ---------------------------------------------------------------- 内部工具

int _toInt(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return int.tryParse(value?.toString() ?? '') ?? 0;
}

double _toDouble(Object? value) {
  if (value is double) return value;
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '') ?? 0.0;
}
