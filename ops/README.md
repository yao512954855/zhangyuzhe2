# 背记系统 —— 部署与运维手册

> 适用：WP-BE-08（F10 / F11 / F8）、部署契约 §11
> 读者假设：**懂 Windows、不懂网络**。照着小节顺序做就能上线。

系统由三块组成，都跑在**同一台局域网 Windows 电脑**上：

| 组件 | 是什么 | 用户怎么访问 |
|---|---|---|
| 后端 | FastAPI + uvicorn，**端口 8810** | — |
| 管理端 | 静态页面，由后端在 `/admin` 下一起发出 | 浏览器开 `http://<本机IP>:8810/admin` |
| 学生端 | 安卓 App（Flutter） | App 通过 UDP 广播自动找到后端 |

**整台机器就是服务器**，没有云、没有域名、不需要域名备案。只要局域网通，就能用。

---

## 1. 端口占用清单

### 1.1 本系统使用的端口

| 端口 | 协议 | 用途 | 谁能不开 |
|---|---|---|---|
| **8810** | TCP | 后端 HTTP（管理端页面 + 全部 API + App 接口） | **必开** |
| **9527** | UDP | App 自动发现服务（广播应答） | 可不开，但 App 就要手工输 IP |
| 5173 | TCP | 仅**开发时**的管理端热更新服务器 | 生产环境不开 |

### 1.2 ⚠️ 本机已被占用的端口（不要撞车）

这台电脑同时跑着**直播课堂**系统，以下端口**已被占用**，`APP_PORT` 绝不能改成它们：

| 端口 | 归属 |
|---|---|
| 9010 | 直播课堂 后端（uvicorn）|
| 8080 | nginx-rtmp HLS |
| 1935 | nginx-rtmp RTMP |
| 8554 | mediamtx RTSP |
| 8889 | mediamtx NVR |
| 8890 | 模拟摄像头 |

> 所以后端端口是 **8810**，不是常见的 8000/8080。改端口要同时改 3 处：
> `backend\.env` 的 `APP_PORT`、**防火墙规则**、以及告诉学生手动输入的地址。
> 管理端不用改（它按当前访问的 host 发请求）。

**改端口前先查一下有没有人占：**

```powershell
Get-NetTCPConnection -LocalPort 8810 -State Listen -ErrorAction SilentlyContinue
```

有输出就是被占了，得先找出是谁：`Get-Process -Id <PID>`。

---

## 2. 首次部署

### 2.1 装环境

| 需要 | 版本 | 说明 |
|---|---|---|
| Python | 3.13 | 后端 |
| Node.js | 22 | **只在打包管理端时需要**，日常运行不需要 |
| NSSM | 任意 | 可选，但**强烈建议**（见 §5）|

### 2.2 放代码 + 配置

```powershell
cd <项目目录>\backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

copy .env.example .env
notepad .env
```

`.env` 里**必须改**的两项（不改后端会拒绝启动，或安全上不可接受）：

| 键 | 怎么填 |
|---|---|
| `JWT_SECRET` | 生成一串随机值：<br>`python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ADMIN_INIT_PASSWORD` | 管理者的初始密码。**首次登录后立刻在管理端改掉** |

其余保持默认即可。要点：
- `APP_PORT=8810`（别动，见 §1.2）
- `DB_PATH=./data/bjsys.db`、`BACKUP_DIR=./backups` —— 相对 `backend\` 目录
- `.env` **不进版本库**、**不要给学生**（里面有密钥）

### 2.3 打包管理端（如果有源码）

```powershell
cd <项目目录>\admin-web
npm ci
npm run build          # 产物在 admin-web\dist
```

把 `dist\` 整个拷到 `backend\static\admin\`（后端在 `/admin` 下托管它）。
**只跑现成服务的话这一步可以跳过** —— 如果 `static\admin` 已经存在就不用管。

### 2.4 第一次启动（手工验证）

```powershell
cd <项目目录>\backend
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8810
```

看到 `Uvicorn running on http://0.0.0.0:8810` 就成了。**另开一个**窗口验一下：

```powershell
Invoke-RestMethod http://127.0.0.1:8810/api/ping
```

应返回类似：

```json
{ "code": 0, "message": "ok",
  "data": { "service": "bjsys", "version": "0.1.0",
            "server_name": "背记系统", "time": "2026-09-24T02:00:00Z" } }
```

> 这 4 个字段名是**契约定死的**（接口设计 §3.1），学生端就是照它们解析的 ——
> 谁改字段名，谁就会让全班的 App 认不出服务器。别动。

再用浏览器打开 `http://127.0.0.1:8810/admin`，用 `admin` / 刚才设的密码登录。
**能看到管理端首页就说明后端 + 管理端都通了。**

**这一步的重要性**：先把本机跑通，再去碰防火墙和服务。混在一起排查会很难受 ——
分不清是「服务没起」还是「防火墙挡了」。

---

## 3. 固定 IP（**这一步别省**）

### 为什么必须做

局域网 IP 由路由器动态分配（DHCP），**路由器重启或租期到了，服务器 IP 就会变**。
IP 一变：

- 学生 App 里存的地址失效 → 全班要用不了
- 管理端书签失效
- 你在路由器上配的端口映射失效

App 端虽然做了自动发现 + 扫码 + 手动输入 + 地址记忆（见 §4），
但**固定 IP 才是从根上消除这类问题**，代价只有 5 分钟。

### 怎么做：在路由器上把 MAC 绑定 IP

1. 查本机网卡 MAC：
   ```powershell
   Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } |
       Select-Object Name, MediaType, MacAddress, LinkSpeed
   ```
   优先选 `MediaType` 是 `802.3`（有线）或 `Native 802.11`（无线）的那块。
   **虚拟网卡（VMware / VirtualBox / Hyper-V / 蓝牙）不要选** —— 绑定它们没意义。
2. 进路由器管理页（一般 `192.168.1.1` 或 `192.168.0.1`），找
   **DHCP 静态地址分配 / 地址保留 / IP-MAC 绑定**（各家叫法不同）。
3. 添一条：MAC = 上面查到的，IP = 想固定的地址（如 `192.168.1.80`）。
   **选一个不在 DHCP 动态池里**的地址，否则可能和别人撞。
4. 在本机把网卡改成固定 IP（**可选**，路由器绑定就够了）：
   `设置 → 网络和 Internet → 以太网 → IP 分配 → 编辑 → 手动`。

> 建议的 IP：`192.168.1.80`、`192.168.1.90` 这类好记的。
> 本项目历史上用过 `192.168.1.80`，管理员熟悉这个地址。

### 免费彩蛋

固定好之后，在教室里**墙上贴一张纸条**：

```
背记系统：http://192.168.1.80:8810/admin
```

老师开浏览器输这个就行，不用记 IP。

---

## 4. 学生端 App 怎么找到服务器

App 上有 4 条路，**按优先级**自动尝试：

| 顺序 | 方式 | 说明 |
|---|---|---|
| 1 | **最近地址记忆** | 上次连上的地址，秒连。日常走这条 |
| 2 | **UDP 广播发现** | 往 `255.255.255.255:9527` 发 `BJSYS_DISCOVER`，服务器回 `BJSYS_HERE` 并带上 8810 端口 |
| 3 | **扫码配置** | 管理端首页有二维码，扫一下就配好 |
| 4 | **手动输入** | 兜底，`192.168.1.x:8810` |

**你（部署者）要做的事**：

- **UDP 广播只在同一网段有效**。学生手机必须和服务器在同一个 WiFi / 同一网段；
  如果教室 WiFi 配了 AP 隔离（客户端之间不能互访），广播就废了 → 那就用扫码或手动输入。
- **防火墙放行 UDP 9527**（见 §5.2），否则广播包直接被系统丢掉、App 永远发现不了。
- App 的发现只认 **8810 这个 HTTP 端口**，不是 9527（9527 只是广播用的）。
  发现报文里 `port` 字段是 8810 —— 这是约定，别搞反。

**怎么验证广播通不通**：在服务器上跑

```powershell
cd <项目目录>\backend
.\.venv\Scripts\python scripts\smoke_be08.py
```

它会真发一次 UDP 广播并检查应答（见 §8）。

---

## 5. 服务化：开机自启（WP-BE-08 F10）

### 5.1 注册服务

**以管理员身份**打开 PowerShell：

```powershell
cd <项目目录>
powershell -ExecutionPolicy Bypass -File ops\install_service.ps1
```

想先看看它要做什么（不动手、不需要管理员）：

```powershell
powershell -ExecutionPolicy Bypass -File ops\install_service.ps1 -DryRun
```

脚本会做 4 件事：

1. 自动找 Python 解释器（优先 `backend\.venv\Scripts\python.exe`）
2. 检查 `.env` 存在、**8810 端口空闲**
3. **先跑一遍迁移 + 启动自检**（DB 可写 / 迁移版本 / 备份目录可写 / UDP 端口可绑）
   —— 把「配置错、库坏了」这类问题在装服务之前暴露出来，别等装完了才起不来
4. 注册服务并启动

### 5.2 两种注册方式的差别（**重要**）

| | NSSM（推荐）| Windows 自带 `New-Service` |
|---|---|---|
| 开机自启 | ✅ | ✅ |
| **崩溃自动重启** | ✅ 5 秒后重起 | ⚠️ 需补一条 `sc.exe failure`，且不如 NSSM 可靠 |
| 日志切割/轮转 | ✅ 自带 | ❌ 要自己接 |
| 装法 | 下 [nssm.cc](https://nssm.cc/download)，把 `nssm.exe` 丢进 `ops\` 目录 | 系统自带 |

**为什么推荐 NSSM**：校园环境下最大的运维风险是「**服务悄悄挂了没人发现**」。
周五下午挂的，周一上课才发现，一整周的记录全丢。NSSM 的崩溃自动重启 + 日志轮转
能把这个风险降到很低。脚本会**自动嗅探** `ops\nssm.exe`，放进去就自动用。

### 5.3 防火墙放行（**必做**）

管理员 PowerShell：

```powershell
# 后端 HTTP（管理端 + App 接口）
netsh advfirewall firewall add rule name="bjsys-api" dir=in action=allow protocol=TCP localport=8810

# App 自动发现（UDP 广播应答）
netsh advfirewall firewall add rule name="bjsys-discovery" dir=in action=allow protocol=UDP localport=9527
```

检查是否生效：

```powershell
netsh advfirewall firewall show rule name="bjsys-api"
```

**只放行私有网络**（更安全）的写法：在「高级安全 Windows Defender 防火墙」
里把规则的**配置文件**限制为「专用」—— 脚本用的是默认（域/专用/公用全放）。
如果这台机器还接着公网，建议手工收紧成「专用」。

### 5.4 日常运维命令

```powershell
Get-Service bjsys-backend                 # 看状态
Restart-Service bjsys-backend             # 重启（改完 .env 一定要重启！）
Stop-Service bjsys-backend                # 停
Get-Content backend\logs\service.log -Tail 50 -Wait   # 跟日志
```

### 5.5 卸载

```powershell
powershell -ExecutionPolicy Bypass -File ops\uninstall_service.ps1
```

**只卸载服务和注册项，不动任何数据**（`data\` / `backups\` / `logs\` 全保留）。

---

## 6. 备份与恢复

### 6.1 自动备份

服务**每天凌晨 02:00** 自动备份一次，产物是 `backend\backups\app_YYYYMMDD.db`。

做法是 SQLite 原生的 `VACUUM INTO`，好处是：

- **不用停服务**，备份期间学生照常答题
- 拿到的是**事务一致的完整副本** —— 不像直接拷 `bjsys.db` 可能拷到写了一半的
  WAL 状态，拷出来的库打不开
- 备份完自动跑一次 `PRAGMA integrity_check`（在副本上跑，不影响线上）

**保留最近 30 份**，超出的按文件名（≈时间序）删最旧的。

### 6.2 手动备份（**升级/大批量导入之前务必执行**）

```powershell
cd <项目目录>
ops\backup.bat
```

或指定后端目录：`ops\backup.bat D:\bjsys\backend`

产物是 `app_YYYYMMDD_HHMMSS.db`（**带秒**）。

> **为什么手动备份不覆盖定时备份**：如果两者同名（`app_20260924.db`），
> 早上手动备一次就把凌晨那份定时备份冲掉了 —— 出问题时反而没有可回退的基准点。
> 所以手动备份一律带秒。

也可以从管理端界面触发（「系统 → 数据备份」），效果一样。

**什么时候该手动备份**：

- ⬆️ 升级后端版本之前
- 📥 期末/开学**批量导入**成绩、内容之前
- 🖥️ 服务器要搬家、重装系统之前
- 🧪 要做什么不确定后果的操作之前

手动备份也**不依赖 HTTP 服务** —— 服务挂了照样能备（直接调后端服务层）。

### 6.3 恢复步骤（**WP-BE-08 F8：故意不提供自动恢复接口**）

> ⚠️ **系统没有「一键恢复」按钮，这是刻意的。**
> 恢复＝把线上库整个换掉，做成 HTTP 接口意味着「一个误点的请求就能把数据换掉」，
> 风险和收益完全不成比例。所以恢复流程写在这里，由人在停机窗口执行。

**恢复步骤（严格按顺序）：**

```powershell
# 1) 停服务 —— 必须先停，否则 db 文件被占用，替换会失败或半途而废
Stop-Service bjsys-backend

# 2) 备份现在的库（哪怕它是坏的，也先留一份，万一要取证/回捞数据）
cd <项目目录>\backend
Move-Item data\bjsys.db data\bjsys.db.broken_20260924

# 3) 删掉 WAL / SHM 两个附属文件 —— 这一步最容易漏！
#    SQLite 的 WAL 里可能还存着比主库更新的数据；不删的话，
#    新换进来的库会被旧的 WAL 覆盖，出现「恢复了个寂寞」的怪象。
Remove-Item data\bjsys.db-wal, data\bjsys.db-shm -Force -ErrorAction SilentlyContinue

# 4) 挑一份好的备份，复制成 data\bjsys.db
Copy-Item backups\app_20260923.db data\bjsys.db

# 5) 先校验这份库是好的（可选但推荐）
.\.venv\Scripts\python -c "import sqlite3;print(sqlite3.connect('data/bjsys.db').execute('PRAGMA integrity_check').fetchone())"
# 输出 ('ok',) 才算好

# 6) 启动服务
Start-Service bjsys-backend
```

**恢复后必须做的验证**（U11 的验收动作）：

1. `Invoke-RestMethod http://127.0.0.1:8810/api/ping` → 有正常返回
2. 管理端登录 → 能看到**恢复那份备份时点**的班级、学生、内容
3. 抽查一个学生的答题记录，确认数据是完整连续的

> 已知限制：恢复是**整库回滚**，恢复到 T 时点，T 之后的答题记录就没了。
> 这是 SQLite 单文件部署的固有代价 —— 所以**备份频率**（每天）+ **重要操作前手动备份**
> 才是数据安全的主要手段，恢复只是最后一道防线。

---

## 7. 常见故障排查

### 7.1 学生 App 连不上

**按顺序查，别跳步：**

| # | 查什么 | 怎么查 | 处理 |
|---|---|---|---|
| 1 | 手机和服务器**同一 WiFi**？ | 手机浏览器开 `http://<服务器IP>:8810/api/ping` | 不在同一网段就换 WiFi；教室 WiFi 有 AP 隔离就得用扫码/手动输入 |
| 2 | 服务在跑吗？ | 服务器上 `Get-Service bjsys-backend` | 没跑 → `Start-Service` |
| 3 | 本机能通吗？ | 服务器上 `Invoke-RestMethod http://127.0.0.1:8810/api/ping` | 本机都不通就是服务问题 → 看 §7.5 日志 |
| 4 | 防火墙放行了吗？ | 别的机器开 `http://<服务器IP>:8810/api/ping` | 不通 → 按 §5.3 放行 TCP 8810 |
| 5 | 自动发现不行 | 看能不能扫码/手动输入连上 | 能就是 UDP 问题 → 放行 UDP 9527；还是不行就用扫码 |
| 6 | 服务器 IP 变了吗？ | 服务器上 `ipconfig` | 变了 → 做 §3 的固定 IP 绑定 |

> **关键分诊**：第 3 步（本机 127.0.0.1）通不通，一刀切开「服务本身的问题」
> 和「网络/防火墙的问题」。别混着查。

### 7.2 管理端页面打不开 / 白屏

- **打不开**：后端没起，或 `backend\static\admin\` 不存在（管理端没拷进去）
- **白屏**：多半是管理端构建产物的 `base` 路径不对 —— 它必须按 **`/admin/`**
  部署（契约 §11）。浏览器按 F12 → Console 看是不是 404 了 js/css
- **登录报错**：先确认能连上后端（§7.1 第 3 步）

### 7.3 打开数据备份时报错

- 先去 `backend\.env` 看 `BACKUP_DIR` 指向哪，确认那个目录**存在且可写**
- 磁盘满了也会失败 —— 看 `Get-PSDrive D`（或数据库所在盘）的剩余空间
- 日志里会有明确原因：`Get-Content backend\logs\service.log -Tail 50`

### 7.4 一键自检（推荐先跑这个）

服务启动时会自动做 4 项自检。想**随时**手工跑一遍：

```powershell
cd <项目目录>\backend
.\.venv\Scripts\python -c "from app.main import bootstrap; from app.tasks.scheduler import run_startup_checks; bootstrap(); [print(r.to_dict()) for r in run_startup_checks()]"
```

检查项：

| 项 | 说明 | 不过怎么办 |
|---|---|---|
| `db_writable` | 数据库可写 | 检查 `data\` 权限、磁盘空间、有没有别的进程独占 |
| `migration_version` | 迁移版本是否最新 | 跑迁移（服务启动会自动跑；手工：`bootstrap()`）|
| `backup_dir` | 备份目录可写 | 建目录 / 改 `BACKUP_DIR` / 修权限 |
| `udp_port` | UDP 9527 可绑 | 端口被别的程序占了 → 查 §1 |

**注意**：除了「DB 不可用」，其他失败**不阻止启动**，只在日志里告警 ——
宁可能用但少个功能的系统，也不要完全打不开的系统。这些失败会出现在
`GET /api/health` 的 `degraded_reasons` 里，管理端可以挂横幅提示。

### 7.5 看日志

| 文件 | 内容 |
|---|---|
| `backend\logs\service.log` | NSSM 抓的 stdout（应用日志）|
| `backend\logs\service.err.log` | NSSM 抓的 stderr（崩溃栈在这里）|
| `backend\logs\` 其他 | 应用自己的按天日志 |

```powershell
Get-Content backend\logs\service.log -Tail 100
Select-String -Path backend\logs\*.log -Pattern 'ERROR' | Select-Object -Last 20
```

---

## 8. 验收自测（WP-BE-08）

部署完想确认「服务发现 + 备份 + 运维」都真的能用，跑真机烟测：

```powershell
cd <项目目录>\backend
.\.venv\Scripts\python scripts\smoke_be08.py
```

它会依次验证：

1. **HTTP 就绪**：`/api/ping` 通、字段齐全
2. **UDP 发现**：真往 `127.0.0.1:9527` 发一次 `BJSYS_DISCOVER`，
   检查收到 `BJSYS_HERE` 且 **`port` 字段是 8810**（不是 9527）
3. **服务信息**：管理员登录取 `/api/server/info`，检查网卡分类与推荐 IP
4. **备份**：调一次手动备份接口，检查产物存在、体积 > 0、校验通过
5. **健康**：`/api/health` 能返回 `degraded_reasons` 字段

> UDP 那一步从 `127.0.0.1` 发是**故意的**：真机烟测在服务器本机跑，
> 不该依赖局域网里再有一台机器。所以服务端对**回环来源**是放行的 ——
> 这是实现上的刻意放宽，不是漏洞（非回环来源仍按私网地址白名单校验）。

---

## 9. 上线检查清单

照着勾一遍，全勾完就算部署完成：

- [ ] `backend\.env` 里 `JWT_SECRET` 已换成随机值
- [ ] `ADMIN_INIT_PASSWORD` 已换，且**首次登录后已在管理端改掉**
- [ ] `.env` 没有被提交到版本库
- [ ] `APP_PORT=8810`（没改成被占用的端口）
- [ ] 管理端 `dist` 已放到 `backend\static\admin\`，浏览器能打开并登录
- [ ] `ops\install_service.ps1` 跑完，服务状态是 `Running`
- [ ] 服务设成**自动启动**（脚本已设，确认一下：`sc.exe qc bjsys-backend`）
- [ ] 防火墙已放行 **TCP 8810** 和 **UDP 9527**
- [ ] 路由器上**绑定 MAC - IP**（§3），或至少确认 IP 暂时稳定
- [ ] 用**另一台设备**（另一台电脑/手机）能打开 `http://<IP>:8810/admin`
- [ ] 学生 App 能自动发现服务器、能登录、能做一次练习
- [ ] `ops\backup.bat` 手工跑一次成功，`backups\` 里有新文件
- [ ] 按 §6.3 **演练一次恢复**（在测试环境做，确认流程走得通）
- [ ] `backend\logs\` 里没有持续的 ERROR
- [ ] 墙上贴了访问地址的纸条 😀

---

## 10. 相关文件

| 文件 | 作用 |
|---|---|
| `backend\.env` | 全部配置（**改配置只改这里**）|
| `backend\.env.example` | 配置模板（带注释）|
| `backend\asr.yaml` | 语音识别厂商配置（AccessKey 只在这里，**不外传**）|
| `ops\install_service.ps1` | 注册 Windows 服务（`-DryRun` 可干跑）|
| `ops\uninstall_service.ps1` | 卸载服务（不动数据）|
| `ops\backup.bat` | 手动备份（不依赖 HTTP 服务）|
| `ops\README.md` | 本文件 |
| `backend\scripts\smoke_*.py` | 各工作包的真机烟测脚本 |
| `docs/背记系统需求说明书.md` | 做什么（含 §11 部署与非功能需求）|
| `docs/dev/01-共享契约冻结.md` | 全员硬约束（§11 部署契约）|
