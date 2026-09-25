/// 正确率趋势图 —— WP-APP-05 F3 / U10，红线 3（**无图表库**，用 `CustomPainter` 手绘）。
///
/// 设计要点：
/// - 数据点、坐标轴、百分比标注全部手绘；
/// - **鲁棒处理两种边界**：空数据（没有任何点）与只有 1 个点都不会崩
///   （U10 专门验这两条）；
/// - 静态绘制，**不闪烁**（共通约束 #3：禁止 >3Hz 动效）。
library;

import 'package:flutter/material.dart';

/// 趋势图上的一个点。`value` 取值 0.0–1.0（正确率）。
@immutable
class TrendPoint {
  const TrendPoint(this.label, this.value)
      : assert(value >= 0 && value <= 1, 'value 必须在 0..1 之间');

  final String label;
  final double value;
}

/// 手绘趋势图。
///
/// - `points` 为空 → 画布留白并显示 [emptyHint]；
/// - 只有 1 个点 → 只画一个点 + 百分比，不连线、不画虚轴；
/// - 多个点 → 连成折线 + 每个点标注百分比。
class TrendChart extends StatelessWidget {
  const TrendChart({
    super.key,
    required this.points,
    required this.lineColor,
    required this.axisColor,
    required this.labelColor,
    required this.hintColor,
    this.emptyHint = '多练几遍，就能看到进步曲线啦',
    this.height = 220,
  });

  final List<TrendPoint> points;
  final Color lineColor;
  final Color axisColor;
  final Color labelColor;
  final Color hintColor;
  final String emptyHint;
  final double height;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: height,
      child: CustomPaint(
        painter: _TrendPainter(
          points: points,
          lineColor: lineColor,
          axisColor: axisColor,
          labelColor: labelColor,
          hintColor: hintColor,
          emptyHint: emptyHint,
          textDirection: Directionality.of(context),
        ),
      ),
    );
  }
}

class _TrendPainter extends CustomPainter {
  _TrendPainter({
    required this.points,
    required this.lineColor,
    required this.axisColor,
    required this.labelColor,
    required this.hintColor,
    required this.emptyHint,
    required this.textDirection,
  });

  final List<TrendPoint> points;
  final Color lineColor;
  final Color axisColor;
  final Color labelColor;
  final Color hintColor;
  final String emptyHint;
  final TextDirection textDirection;

  static const double _paddingLeft = 8.0;
  static const double _paddingRight = 8.0;
  static const double _paddingTop = 28.0;
  static const double _paddingBottom = 28.0;

  @override
  void paint(Canvas canvas, Size size) {
    final width = size.width;
    final height = size.height;
    final plotLeft = _paddingLeft;
    final plotRight = width - _paddingRight;
    final plotTop = _paddingTop;
    final plotBottom = height - _paddingBottom;
    final plotW = (plotRight - plotLeft).clamp(0.0, double.infinity);
    final plotH = (plotBottom - plotTop).clamp(0.0, double.infinity);

    if (points.isEmpty) {
      _drawHint(canvas, size, plotTop);
      return;
    }

    // 横向网格 + 百分比刻度（0/25/50/75/100）。
    final gridPaint = Paint()
      ..color = axisColor
      ..strokeWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      final y = plotBottom - plotH * (i / 4);
      canvas.drawLine(Offset(plotLeft, y), Offset(plotRight, y), gridPaint);
      _drawText(
        canvas,
        '${i * 25}%',
        Offset(plotLeft, y - 6),
        _tinyStyle(labelColor),
        alignRight: false,
      );
    }

    final n = points.length;
    double xAt(int i) {
      if (n == 1) {
        return (plotLeft + plotRight) / 2;
      }
      return plotLeft + plotW * (i / (n - 1));
    }

    double toY(double v) => plotBottom - plotH * v.clamp(0.0, 1.0);

    // 折线（>=2 个点才画）。
    if (n >= 2) {
      final linePaint = Paint()
        ..color = lineColor
        ..strokeWidth = 2
        ..style = PaintingStyle.stroke;
      final path = Path();
      for (var i = 0; i < n; i++) {
        final x = xAt(i);
        final y = toY(points[i].value);
        if (i == 0) {
          path.moveTo(x, y);
        } else {
          path.lineTo(x, y);
        }
      }
      canvas.drawPath(path, linePaint);
    }

    // 数据点 + 百分比标注。
    final dotPaint = Paint()..color = lineColor;
    for (var i = 0; i < n; i++) {
      final x = xAt(i);
      final y = toY(points[i].value);
      canvas.drawCircle(Offset(x, y), 4, dotPaint);
      _drawText(
        canvas,
        '${(points[i].value * 100).round()}%',
        Offset(x, y - 18),
        _pctStyle(lineColor),
        alignCenter: true,
      );
      // X 轴标签（仅 1 个或 ≤6 个时全标，避免拥挤）。
      if (n <= 6 || i == 0 || i == n - 1) {
        _drawText(
          canvas,
          points[i].label,
          Offset(x, plotBottom + 8),
          _tinyStyle(labelColor),
          alignCenter: true,
        );
      }
    }
  }

  void _drawHint(Canvas canvas, Size size, double top) {
    final text = _paragraph(emptyHint, _tinyStyle(hintColor));
    final x = (size.width - text.width) / 2;
    final y = top + (size.height - top - _paddingBottom - text.height) / 2;
    text.paint(
      canvas,
      Offset(x.clamp(0.0, size.width), y.clamp(0.0, size.height)),
    );
  }

  TextPainter _paragraph(String text, TextStyle style) {
    final tp = TextPainter(
      text: TextSpan(text: text, style: style),
      textDirection: textDirection,
    );
    tp.layout();
    return tp;
  }

  void _drawText(
    Canvas canvas,
    String text,
    Offset offset,
    TextStyle style, {
    bool alignCenter = false,
    bool alignRight = false,
  }) {
    final tp = _paragraph(text, style);
    var dx = offset.dx;
    if (alignCenter) {
      dx = offset.dx - tp.width / 2;
    } else if (alignRight) {
      dx = offset.dx - tp.width;
    }
    tp.paint(canvas, Offset(dx, offset.dy));
  }

  TextStyle _tinyStyle(Color c) =>
      TextStyle(fontSize: 12, color: c, height: 1.2);

  TextStyle _pctStyle(Color c) =>
      TextStyle(fontSize: 13, fontWeight: FontWeight.w500, color: c);

  @override
  bool shouldRepaint(covariant _TrendPainter old) =>
      old.points != points ||
      old.lineColor != lineColor ||
      old.axisColor != axisColor ||
      old.labelColor != labelColor ||
      old.hintColor != hintColor;
}
