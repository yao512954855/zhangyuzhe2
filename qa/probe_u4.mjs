#!/usr/bin/env node
/**
 * 一次性探针：验证「自主注册（未分班）的学生是否会出现在 `class_id=unassigned` 列表里」。
 * 不进仓库交付物，只用于定位 WEB-03 烟测 U4 的失败原因。
 */
const B = `http://127.0.0.1:${process.env.PROBE_PORT || 8818}`

async function call(path, { method = 'GET', token, body } = {}) {
  const headers = {}
  if (token) headers.Authorization = `Bearer ${token}`
  if (body) headers['Content-Type'] = 'application/json'
  const r = await fetch(`${B}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined })
  return r.json()
}

const admin = await call('/api/auth/login', {
  method: 'POST',
  body: { username: 'admin', password: 'probe-pass-12345', role: 'admin' },
})
if (admin.code !== 0) {
  console.log('admin login failed', admin)
  process.exit(1)
}
const t = admin.data.access_token

const reg = await call('/api/auth/register', {
  method: 'POST',
  body: { username: 'probeSelf01', real_name: '探针学生', password: 'probe-pass-12345' },
})
console.log('register:', JSON.stringify(reg))

const all = await call('/api/students?page=1&page_size=100', { token: t })
console.log('all students total =', all.data.total, 'items =', all.data.items.map((s) => `${s.username}/${s.class_id}/${s.register_source}`))

const un = await call('/api/students?page=1&page_size=100&class_id=unassigned', { token: t })
console.log('unassigned total =', un.data.total)
console.log('unassigned items =', JSON.stringify(un.data.items.map((s) => `${s.username}/${s.class_id}/${s.register_source}`)))

const kw = await call('/api/students?page=1&page_size=20&keyword=probeSelf01', { token: t })
console.log('keyword total =', kw.data.total, JSON.stringify(kw.data.items.map((s) => `${s.username}/${s.class_id}`)))
