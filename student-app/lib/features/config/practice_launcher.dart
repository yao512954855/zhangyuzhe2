/// 把一次场次送进练习页 —— **首页快捷开始与配置页共用这一份**。
///
/// 为什么单独抽出来：F6（单元卡直接开练）与 F13（配置页发起）最终都要
/// "拿着 `PracticeSession` 跳练习页"。写两份的话，将来 APP-03 落地时
/// 很容易只改到其中一处，另一处继续弹"还在开发中"。
library;

import 'package:flutter/material.dart';

import '../../core/theme/age_profile.dart';
import '../../router/routes/practice.dart';
import '../../router/routes/recite.dart';
import 'practice_session.dart';

/// 跳到练习页；**两个练习页都还没落地时如实告知**（绝不假装成功）。
///
/// 这条"未落地分支"不是临时凑数：它让 APP-02 自己的验收
/// （U6/U7：题量与顺序符合预期）**可以独立确认** ——
/// 弹层里明明白白写着"已经组好 N 道题"，组卷链路是真的通了。
void openPracticeSession(BuildContext context, PracticeSession session) {
  final isEnglish = session.contentType == 'english';
  final route = isEnglish ? PracticeRoutes.practice : ReciteRoutes.recite;
  final registered = isEnglish
      ? PracticeRoutes.routes.containsKey(route)
      : ReciteRoutes.routes.containsKey(route);

  if (registered) {
    Navigator.of(context).pushNamed(route, arguments: session);
    return;
  }

  showDialog<void>(
    context: context,
    builder: (dialogContext) {
      final profile = AgeScope.of(dialogContext);
      return AlertDialog(
        title: const Text('这次已经准备好啦'),
        content: Text(
          '已经组好 ${session.itemCount} 道题'
          '${session.unitName == null ? '' : '（${session.unitName}）'}。\n'
          '${isEnglish ? '英语练习页' : '语文背诵页'}还在开发中，等它做好就能直接开练。',
          style: profile.bodyStyle,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: const Text('知道了'),
          ),
        ],
      );
    },
  );
}
