# QA-01 测试数据（fixtures）

集成与验收用的固定输入。**全部由 `make_fixtures.py` 生成，不要手改 xlsx** ——
改完下次跑脚本就被覆盖，而且二进制文件在 code review 里根本看不出改了什么。

```bash
cd qa/fixtures
../../backend/.venv/Scripts/python.exe make_fixtures.py
```

> ⚠️ 本机跑后端相关命令一律加 `env -u PYTHONPATH`。WorkBuddy 沙箱把 safe-delete shim
> 注进了 `PYTHONPATH`，它 hook 了 `unlink` 并用 `raise SystemExit(1)` 拒绝，
> 会直接把 spawn 出来的进程杀掉（症状：子进程一行日志都没有）。

## 文件

| 文件 | 行数 | 预期校验结果 | 用途 |
|---|---|---|---|
| `students_import.xlsx` | 45 | `valid=43 / invalid=2` | 验收矩阵 #5 / U2「导入 45 名学生（2 行错误）→ `created=43`」 |
| `words_import.xlsx` | 9 | `valid=8 / invalid=1` | 验收矩阵 #8；混两个单元，验「自动建缺失单元」 |
| `texts_import.xlsx` | 1 | `valid=1 / invalid=0` | 验收矩阵 #9；2 段，背诵范围留空 = 全文 |

列定义与预期值都从 `make_fixtures.py` 里算出来打印，**不要手写到文档里当断言** ——
手写的数字一旦和生成逻辑脱节，就成了一份「看着像断言、其实是幻觉」的文档。

**故意的非法行**（用来钉住「错误行定位到具体行」这件事，不是写错了）：

| 文件 | 行号 | 原因 |
|---|---|---|
| `students_import.xlsx` | 第 8 行 | 姓名空 |
| `students_import.xlsx` | 第 30 行 | 学号 40 个字符，超过 32 上限 |
| `words_import.xlsx` | 第 11 行 | 年级 10 越界（合法范围 1–9） |

## 音频样本不在这里

ASR 预研留下的真实音频在别处，**不要再拷一份**（78 个 wav，拷了就是两份会漂的数据）：

| 目录 | 内容 |
|---|---|
| `qa/audio` | 英语：整词 / 逐字母拼读 / 音节切分对照样本 |
| `qa/audio_cn` | 语文课文朗读片段 |
| `qa/audio_neg` | **负样本**（错读），用于「忠实度」测试 —— 判分模型绝不能把它们「纠正」成正确词 |

## 与谁配对使用

- 冒烟：`qa/smoke/smoke.sh`（自己内部造数，不读本目录）
- 端到端：`qa/e2e/e2e.sh`（同上）
- 本目录的 fixture 主要给**人工验收**与**包级烟测**用：
  `backend/scripts/smoke_be02.py`（名单）、`smoke_be03.py`（词条/课文）
