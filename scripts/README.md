# word2excel.py —— Word 导入转换脚本（WP-2B）

把 `.docx` 转换成**符合背记系统现有 Excel 导入模板**的 `.xlsx`，从而复用一期已成熟的
Excel 导入链路（`backend/app/api/imports.py` + `backend/app/services/import_service.py`）。

> **本项目不新增任何后端接口、不改导入链路、不改迁移。** 只是「docx → xlsx」的搬运工，
> 产出的 xlsx 直接走现成的 `POST /api/import/{words,texts}/preview` + `commit`。

---

## 1. 权威契约来源

列名 / 必填 / 枚举口径**严格对齐** `backend/app/services/import_service.py`
（而非需求说明书《附录 A》—— 那是简化示意图，直接照它产文件会因缺列/列名不符而导入失败）。

### 1.1 英语词条（14 列，必需 6 列）

| 列序 | 列名 | 必需 | 约束 |
|---|---|---|---|
| 1 | 学科 | ✅ | 语文 / 英语 |
| 2 | 年级 | ✅ | 1–9 |
| 3 | 学期 | ✅ | 上学期 / 下学期 |
| 4 | 单元名 | ✅ | 文本 |
| 5 | 单元序号 | | 整数 0–9999 |
| 6 | 类型 | ✅ | 单词 / 短语 / 句子（导入端逐行校验必填，缺则报「类型不能为空」） |
| 7 | 英文 | ✅ | 文本 |
| 8 | 中文 | ✅ | 文本 |
| 9 | 可接受答案 | | 多值，分隔符 `\|,，、；;` 均可 |
| 10 | 音标 | | 文本 |
| 11 | 词性 | | 文本 |
| 12 | 例句英 | | 文本（**保留中文标点，不转半角**） |
| 13 | 例句中 | | 文本（同上） |
| 14 | 是否拼读 | | 是 / 否；缺省时「单词→是，短语/句子→否」 |

### 1.2 语文课文（8 列，必需 6 列）

| 列序 | 列名 | 必需 | 约束 |
|---|---|---|---|
| 1 | 学科 | ✅ | 语文 / 英语 |
| 2 | 年级 | ✅ | 1–9 |
| 3 | 学期 | ✅ | 上学期 / 下学期 |
| 4 | 单元名 | ✅ | 文本 |
| 5 | 课文标题 | ✅ | 文本 |
| 6 | 作者 | | 文本 |
| 7 | 段落正文 | ✅ | 文本（**保留中文标点与换行**） |
| 8 | 背诵范围 | | 见 §4；留空 = 全部段落 |

---

## 2. 依赖

- `openpyxl` —— 已在 `backend/requirements.txt` 中（后端 venv 自带）。
- `python-docx` —— **尚未登记到 `backend/requirements.txt`**（该文件为共享文件，本工作包不独占）。
  请在 `backend/.venv` 中安装：

  ```bash
  env -u PYTHONPATH backend/.venv/Scripts/python.exe -m pip install python-docx
  ```

  > ⚠️ 待办：由 `backend/requirements.txt` 的负责方把 `python-docx==1.2.0` 登记进依赖清单
  > （写死版本，契约 §1），否则换环境后需重新手动安装。参见文末「遗留问题」。

---

## 3. 用法

```bash
# 英语词条（表格结构，文档里已含 学科/年级/学期/单元名 列）
python word2excel.py lesson.docx lesson.xlsx --type words

# 英语词条（Word 没写 学科/年级/学期/单元名，用 CLI 补全）
python word2excel.py lesson.docx lesson.xlsx --type words \
    --subject 英语 --grade 3 --semester 上学期 --unit "Unit 1"

# 语文课文（标签块结构，文档无表格）
python word2excel.py text.docx text.xlsx --type texts --format block

# 强制按表格解析（即使文档里也有标签块）
python word2excel.py x.docx x.xlsx --type words --format table
```

### 参数

| 参数 | 说明 |
|---|---|
| `input` | 输入 `.docx` 路径（**必填，命令行位置参数**） |
| `output` | 输出 `.xlsx` 路径（**必填，命令行位置参数**） |
| `--type` | `words` / `texts`（**必填**） |
| `--format` | `auto`（默认，有表格用表格否则标签块）/ `table` / `block` |
| `--subject` / `--grade` / `--semester` / `--unit` / `--unit-order` | 全局默认元数据，用于补全缺列 |

> 所有路径均通过命令行传入，**脚本内不硬编码任何路径**。

---

## 4. 支持的 Word 结构

### 4.1 表格（推荐，1:1 映射，最不容易出错）

- 文档里**每个表格独立解析**：首行=表头，其后每行=一条记录。
- 表头列名用别名也行：`单元`→单元名、`标题`→课文标题、`正文`→段落正文、
  `en`→英文、`cn`→中文、`subject`→学科 等（完整别名见脚本 `COLUMN_ALIASES`）。
- **文档里多个表格的行会累加**到同一个 xlsx（例如一个单元一个表）。
- 若表格缺 `学科/年级/学期/单元名`，用 `--subject/--grade/--semester/--unit` 补全。

### 4.2 标签块（无表格时回退）

- 整篇按**空段落**切成记录；每条记录内每行 `列名：值`（中/英文冒号均可）。
- 一条**只含共享元数据**（学科/年级/学期/单元名/单元序号）的记录会被当作**全局默认值**，
  应用到其后的所有记录；记录自身也可覆盖。
- 无冒号的**续行**会拼到当前字段（用于「段落正文」跨多段的正文）。

### 4.3 枚举归一化（保证产出可导入）

| 字段 | 接受输入 | 产出到 xlsx |
|---|---|---|
| 学科 | 语文/中文/chinese/英语/英文/english | 语文 / 英语 |
| 年级 | 3 / 三年级 / grade 3 / G3 | 整数 1–9（越界报错） |
| 学期 | 上/上学期/first/1 / 下/下学期/second/2 | 上学期 / 下学期 |
| 类型 | 单词/word/短语/phrase/句子/sentence | 单词 / 短语 / 句子 |
| 是否拼读 | 是/true/1/√ / 否/false/0/× | 是 / 否 |
| 背诵范围 | 见下 | 见下 |

**背诵范围约定（人写 1-based，导入端按 0-based 索引段落）：**

- `全部` / `全` / 空 → 空单元格（后端=全部段落）。
- `1,3` → `"0,2"`；`1-3` → `"0,1,2"`（含端点、自动排序去重）。
- 含无法解析的标记（如字母）→ **直接报错**，不静默产出废文件。

---

## 5. 不支持 / 会报错的结构

- **文本框、页眉页脚、图文框内文字**：python-docx 读不到，会漏数据 → 报错建议改用普通表格。
- **嵌套表格、合并单元格跨列**：仅取该单元格文本，跨列语义不展开。
- **一个 docx 混用「表格 + 标签块」且希望二者合并**：默认 `auto` 表格优先，
  标签块仅在文档**没有任何表格**时启用。确有混合需求请拆成两个 docx。
- **列名非法 / 必填缺失 / 年级越界 / 枚举非法 / 背诵范围格式错**：一律 `SystemExit(1)`
  并给出可改提示（「宁可在转换时报错，也不产出导入必失败的 xlsx」）。

---

## 6. 验证方式（自测）

造样例 docx → 转换 → 逐列核对：

```bash
# 用 python-docx 造样例（见 qa/tmp/make_samples.py）
env -u PYTHONPATH backend/.venv/Scripts/python.exe qa/tmp/make_samples.py

# 转换
env -u PYTHONPATH backend/.venv/Scripts/python.exe scripts/word2excel.py \
    qa/tmp/sample_words_table.docx qa/tmp/out_words_table.xlsx --type words
env -u PYTHONPATH backend/.venv/Scripts/python.exe scripts/word2excel.py \
    qa/tmp/sample_words_block.docx qa/tmp/out_words_block.xlsx --type words --format block
env -u PYTHONPATH backend/.venv/Scripts/python.exe scripts/word2excel.py \
    qa/tmp/sample_texts_table.docx qa/tmp/out_texts_table.xlsx --type texts

# 逐列核对：读取 xlsx 的表头，应与 import_service.py 的 WORDS_HEADERS / TEXTS_HEADERS 完全一致
```

脚本对必填、枚举、年级范围、背诵范围都做了前置校验，因此**产出的 xlsx 可直接被现有
导入接口消费**，无需改任何后端代码。

---

## 7. 已知局限 / 遗留问题

1. **`python-docx` 未登记进 `backend/requirements.txt`**：该文件为共享文件，本工作包不独占，
   未擅自修改。需由该清单负责方补登 `python-docx==1.2.0`（契约 §1 要求写死版本）。
2. **模板下拉有效性不生成**：产出的 xlsx 只有表头+数据，没有 `数据有效性下拉`。
   导入端按列名匹配、不依赖下拉，故不影响导入；如需与「下载模板」完全一致的体验可后续补。
3. **背诵范围的 1-based 假设**：脚本默认人写的段落号是 1-based（第1段=1）。
   若你的源数据本身就是 0-based，请改用逗号列表并在脑中换算，或改造 `--scope-base`。
4. **不调用后端做端到端落库**：本脚本只负责「docx→xlsx」。端到端入库需配合现有
   `preview`/`commit` 接口（属一期导入链路，本包不改）。自测聚焦「列与校验对齐」。
5. **Word 结构需规范**：自由格式（标签块）依赖 `列名：值` 约定，建议在 README 约定确定后
   由各学科老师按模板填写，或优先使用「表格」结构。
