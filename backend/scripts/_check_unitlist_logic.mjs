/**
 * 模拟验证 UnitList.vue 的树数据流（纯逻辑复刻，非 import —— 组件依赖 vue 运行时）。
 * 验证：树各层级节点点击 → filters → buildQuery 的过滤参数是否正确。
 */
const SUBJECT_LABEL = { english: '英语', chinese: '语文' }
const SEMESTER_LABEL = { first: '上学期', second: '下学期' }

function buildTree(items) {
  const root = { key: 'all', label: '全部单元', kind: 'all', children: [] }
  const grades = [...new Set(items.map((i) => i.grade))].sort((a, b) => a - b)
  for (const g of grades) {
    const gNode = { key: `g:${g}`, label: `${g} 年级`, kind: 'grade', grade: g, children: [] }
    for (const sem of ['first', 'second']) {
      const semItems = items.filter((i) => i.grade === g && i.semester === sem)
      if (semItems.length === 0) continue
      const semNode = { key: `sem:${g}:${sem}`, label: SEMESTER_LABEL[sem], kind: 'semester', grade: g, semester: sem, children: [] }
      for (const s of ['english', 'chinese']) {
        const unitList = semItems.filter((i) => i.subject === s).sort((a, b) => a.order_no - b.order_no)
        if (unitList.length === 0) continue
        semNode.children.push({
          key: `s:${g}:${sem}:${s}`, label: SUBJECT_LABEL[s], kind: 'subject', subject: s, grade: g, semester: sem,
          children: unitList.map((u) => ({ key: `u:${u.id}`, label: u.name, kind: 'unit', unitId: u.id })),
        })
      }
      if (semNode.children.length) gNode.children.push(semNode)
    }
    if (gNode.children.length) root.children.push(gNode)
  }
  return [root]
}

function flatTree(nodes, out = []) {
  for (const n of nodes) { out.push(n); if (n.children) flatTree(n.children, out) }
  return out
}

// 模拟数据：英语 3上 2 个 + 英语 7上 2 个 + 语文 7上 1 个 + 语文 7下 1 个
const treeOptions = [
  { id: 1, name: 'Unit 1 Hello!', subject: 'english', grade: 3, semester: 'first', order_no: 1 },
  { id: 2, name: 'Unit 2 School', subject: 'english', grade: 3, semester: 'first', order_no: 2 },
  { id: 5, name: 'Unit 1 Hello!', subject: 'english', grade: 7, semester: 'first', order_no: 1 },
  { id: 7, name: 'Unit 2 My week', subject: 'english', grade: 7, semester: 'first', order_no: 2 },
  { id: 3, name: '第一单元', subject: 'chinese', grade: 7, semester: 'first', order_no: 1 },
  { id: 6, name: '背影', subject: 'chinese', grade: 7, semester: 'second', order_no: 1 },
]

const treeData = buildTree(treeOptions)
const all = flatTree(treeData)

let failures = 0
function check(name, cond, detail = '') {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ' + detail}`)
  if (!cond) failures++
}

// 树结构断言
check('树根为「全部单元」', treeData[0].key === 'all')
check('年级节点 = 3 年级、7 年级（升序）', JSON.stringify(treeData[0].children.map((n) => n.label)) === JSON.stringify(['3 年级', '7 年级']))
const g7 = treeData[0].children[1]
check('7 年级下学期节点 = 上学期、下学期', JSON.stringify(g7.children.map((n) => n.label)) === JSON.stringify(['上学期', '下学期']))
const g7first = g7.children[0]
check('7 年级上学期下学科 = 英语、语文', JSON.stringify(g7first.children.map((n) => n.label)) === JSON.stringify(['英语', '语文']))
check('3 年级无语文（空分支裁掉）', treeData[0].children[0].children[0].children.every((n) => n.label !== '语文'))

// 模拟点击 → filters → buildQuery
let filters = { subject: '', grade: '', semester: '', status: '' }
let selectedUnit = null
function onNodeClick(node) {
  if (node.kind === 'unit') { selectedUnit = treeOptions.find((o) => o.id === node.unitId); return }
  selectedUnit = null
  if (node.kind === 'all') { filters = { subject: '', grade: '', semester: '', status: '' } }
  else if (node.kind === 'grade') { filters = { subject: '', grade: node.grade, semester: '', status: '' } }
  else if (node.kind === 'semester') { filters = { subject: '', grade: node.grade, semester: node.semester, status: '' } }
  else if (node.kind === 'subject') { filters = { subject: node.subject, grade: node.grade, semester: node.semester, status: '' } }
}
function buildQuery() {
  const q = {}
  if (filters.subject) q.subject = filters.subject
  if (filters.grade !== '') q.grade = filters.grade
  if (filters.semester) q.semester = filters.semester
  return q
}

const byKey = (k) => all.find((n) => n.key === k)

function sortedJson(o) { return JSON.stringify(o, Object.keys(o).sort()) }
onNodeClick(byKey('s:7:first:english'))
check('点「7年级上学期·英语」→ query 只筛出英语', sortedJson(buildQuery()) === sortedJson({ grade: 7, semester: 'first', subject: 'english' }), JSON.stringify(buildQuery()))

onNodeClick(byKey('g:3'))
check('点「3 年级」→ 只筛年级', JSON.stringify(buildQuery()) === JSON.stringify({ grade: 3 }), JSON.stringify(buildQuery()))

onNodeClick(byKey('sem:7:second'))
check('点「7年级·下学期」→ 年级+学期', JSON.stringify(buildQuery()) === JSON.stringify({ grade: 7, semester: 'second' }), JSON.stringify(buildQuery()))

onNodeClick(byKey('all'))
check('点「全部单元」→ 清空筛选', JSON.stringify(buildQuery()) === JSON.stringify({}), JSON.stringify(buildQuery()))

onNodeClick(byKey('u:3'))
check('点单元叶子 → 进入单元视图（不跳路由）', selectedUnit && selectedUnit.id === 3 && selectedUnit.subject === 'chinese', JSON.stringify(selectedUnit))

// currentKey 推导
function currentKey() {
  if (selectedUnit) return `u:${selectedUnit.id}`
  const { subject, grade, semester } = filters
  if (subject && grade !== '' && semester) return `s:${grade}:${semester}:${subject}`
  if (grade !== '' && semester) return `sem:${grade}:${semester}`
  if (grade !== '') return `g:${grade}`
  if (subject) return `x:${subject}`
  return 'all'
}
check('单元视图 currentKey = u:3', currentKey() === 'u:3')
selectedUnit = null
check('全空筛选 currentKey = all', currentKey() === 'all')
filters = { subject: 'english', grade: 7, semester: 'first', status: '' }
check('英语 7 上 currentKey = s:7:first:english', currentKey() === 's:7:first:english')
check('该 key 在树上真实存在（高亮可命中）', !!byKey(currentKey()))

process.exit(failures ? 1 : 0)
