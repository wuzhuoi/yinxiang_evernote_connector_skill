---
name: yinxiang-connector
description: 印象笔记（Yinxiang / Evernote 国内版）连接器与备份同步能力包。提供 MCP 连接器（检索笔记 + 把 Markdown 回写为「原生 Markdown 笔记」，客户端可切换源码/预览）以及把任意文件夹批量备份到印象笔记的脚本：.md 独立成篇存为原生 Markdown 笔记，其他文件按扩展名分组以附件形式贴在「超级笔记」里，图片合并为一条内联笔记。当用户提到"存到印象笔记 / 从印象笔记读取 / 搜索我的印象笔记 / 备份文件夹到印象笔记 / 把笔记同步到印象笔记 / 在 WorkBuddy 或 Trae 里接入印象笔记"时使用。跨平台（Windows / macOS）、跨客户端（WorkBuddy / Trae / 任意支持 stdio MCP 的客户端）。
agent_created: true
---

# 印象笔记（Yinxiang）连接器 · 回写与备份

## Overview

把印象笔记（国内版 `app.yinxiang.com`）接入 AI 客户端，提供三类能力：

1. **MCP 连接器（`server.py`）**：暴露 4 个工具，让 AI 在对话中检索笔记、把产出回写为**原生 Markdown 笔记**（在印象笔记客户端里可切换「编辑 / 预览」、查看原始源码）。
2. **批量备份脚本（`yinxiang_backup.py`）**：把一个文件夹整体同步到印象笔记——
   - `.md` 文件 → 每个独立成一条**原生 Markdown 笔记**（保留源码，相对路径作标题）；
   - 其他文件（docx / pdf / pptx / xlsx / html / zip …）→ 按扩展名分组、组内按账户体积上限自动分批，文件作为**附件**贴在一条**「超级笔记」**里（正文用 `<en-media>` 引用，客户端可点击下载）；
   - 图片（jpg / png …）→ 全部合并为一条内联笔记；
   - `.py` / `.rar` 等默认跳过；`.git` / `.venv` 等隐藏目录自动排除。
3. **共享模块（`yinxiang_md.py`）**：笔记格式构造（原生 Markdown 笔记 / 超级笔记）+ ENML 归一化，被上面两者复用。

底层依赖 `zibuyu_evernote`（Python 3 版 Evernote EDAM SDK）+ FastMCP。配置全走环境变量，无硬编码路径。

---

## ⚠️ Token 有效期与续期（重要，2026-09-30 补）
- **Developer Token（从 DeveloperToken.action 获取）是短期凭证**：印象笔记（国内版）页面显示其有效期约 **7 天**，且只在生成时显示一次、需手动 Revoke 后重发。自动化的 Developer Token 会每 ~7 天失效（`EDAMUserException errorCode=9`），无法无人值守续期。
- **OAuth Access Token（官方推荐用于生产）**：默认有效期 **1 年**（`edam_expires` 字段携带过期时间；授权时可被用户调成 1 天/1 周/1 月，**务必保留默认 1 年**）。印象笔记官方明确：应用投入实际使用时用户应通过 OAuth 认证，Developer Token 仅用于开发期联调。
- **切换成本极低**：底层 `zibuyu_evernote` EDAM SDK 接受任意 `S=` 开头 token，Developer Token 与 OAuth Access Token 共用同一个 `mcpServers.yinxiang.env.EVERNOTE_TOKEN` 字段。因此"治本"做法 = 注册 API Key（Consumer Key/Secret）→ 跑一次 OAuth 1.0a 拿到 1 年有效 token → 同字段替换，**连接器代码无需改动**。
- **治标方案（不想注册 API Key 时）**：保留 Developer Token，但设每 ~6 天的人/半自动续期（Playwright 登录点击生成并抓取一次性 token，或定时提醒人工替换）。
- 健壮性建议：无论哪种 token，运行前都先做最小连通性测试（如 `yinxiang_list_notebooks`），失败时 fail-soft 通知并终止，不要盲目重试上传。

### skills-oauth 正确获取方式（实测 2026-09-30，关键）
- 打开 `https://app.yinxiang.com/third/skills-oauth/`（需登录 + **付费会员**：仅对 PLUS/PREMIUM/PRO/SUPER_VIP 开放，免费账户会弹"仅对会员开放"升级提示）。
- 页面真正的按钮是 **`#generate-token-btn`**（文字「生成新 Token」，onclick=`authorizeWithMembershipCheck('yinxiang-ai-skill')`）。点击 → 校验会员 → 跳 `/third/skills-oauth/auth/yinxiang-ai-skill` 生成 token → 带 `?token=...` 跳回并显示在 **`#token-code`** 元素（含「复制」按钮）。**不要点页面里其它「授权」链接**——那会跳到授权管理页、拿不到 token。
- 返回的 token 形如 `S=s45:U=...:A=yinxiang-skill:V=2:H=...`，是经 `/third/mcp-oauth/callback` 换发的**正式 OAuth access token，有效期约 1 年**（实测 `E−C ≈ 365 天`），彻底解决 Developer Token 7 天失效问题。
- 自动化抓取要点：用浏览器驱动登录态 → 点 `#generate-token-btn` → **扫描整页内容**（`page.content()`，含 `urllib.parse.unquote` 解码后）匹配 `S=s\d+:U=...` 提取 token。切勿只查 `#token-code`（回调页上该元素可能不存在，会漏抓），也切勿抓 `text=授权` 等其它元素。
- 复用脚本：**`yinxiang_oauth_token.py`**（可选组件，需 `pip install playwright`）。`python yinxiang_oauth_token.py --profile <独立档案目录>` 会开可见浏览器、等你登录、点按钮、抓 token；加 `--write` 直接写进 mcp.json；手动复制到的 token 可用 `--token 'S=...' --write` 回写。抓不到时保留截图 `oauth_debug.png` 供人工兜底。

### ⚠️ 备份脚本同名跳过碰撞（已知坑）
- `yinxiang_backup.py` 对附件/图片批次用**通用标题**（如 `文件备份 - pdf（2个）`）。若目标笔记本已存在同标题旧笔记，脚本会误判「已存在」而**跳过，实际未上传**（静默成功！）。若工作区新增文件但笔记本里查不到，应改用**唯一标题（文件相对路径含扩展名）**强制建笔记，不要依赖默认跳过逻辑。验证应以 API 查标题+附件数（非脚本"跳过"报喜）为准。

## ⚠️ 首次接入引导（必须按顺序执行）

**接入前，第一件事是先问用户账户级别**——它直接决定单条笔记体积上限与分批策略，选错会导致上传失败。

### 第一步：确认账户级别（先问用户）

向用户提问：**"你的印象笔记是什么账户级别？（免费 / 标准 / 高级 / 专业）"**，然后按下表取用：

| 账户级别 | `--plan` 参数 | 单条笔记大小上限 | 每月上传流量 |
|---|---|---|---|
| 免费帐户 | `free` | 25 MB | 60 MB |
| 标准帐户 | `standard` | 50 MB | 1 GB |
| 高级帐户 | `premium` | 200 MB | 10 GB |
| 专业帐户 | `professional` | 300 MB | 20 GB |

> 数据来源：印象笔记帮助中心（https://help.yinxiang.com/hc/articles/63016），以官网最新为准。
> 备份脚本会据此设置分批体积与单文件跳过阈值；**默认按最保守的 `free` 处理**。

### 第二步：拿到 API Key（Developer Token）

引导用户：
1. 浏览器登录网页版 `https://app.yinxiang.com`；
2. **手动访问**这个不在菜单里的地址：`https://app.yinxiang.com/api/DeveloperToken.action`；
3. 点 **"Create a developer token"**，复制生成的 `S=` 开头字符串（**只显示一次**）；
4. 若该页不可用，改用 AI 授权页：`https://app.yinxiang.com/third/skills-oauth/`。

### 第三步：把 Key 放进配置文件

写进客户端的 MCP 配置 `mcp.json` → `mcpServers.yinxiang.env.EVERNOTE_TOKEN`（详见 `README.md` 第二节）。
**提醒用户：Token 属账户级凭证，不要提交到仓库或同步盘。**

### 第四步：连接 MCP（关键按钮）

- **WorkBuddy**：打开**连接器管理**页 → 点**右上角的「自定义连接器」** → 找到刚写入的 `yinxiang` → 点 **信任 / 启用** → 重启客户端。
- **Trae**：设置 → MCP → 添加 → 手动添加，粘贴配置 → 重启（详见 README）。

### 第五步：验证

新会话里说"列出我的印象笔记笔记本"，能返回列表即接入成功。

---

## Workflow

### A. MCP 连接器（让 AI 在对话里读写印象笔记）

1. 建隔离 venv 并安装依赖：
   ```bash
   python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. 在客户端 `mcp.json` 注册（`command` 用 venv 里的 python 绝对路径）：
   ```json
   {
     "mcpServers": {
       "yinxiang": {
         "command": "/绝对路径/.venv/bin/python",
         "args": ["/绝对路径/yinxiang-connector/server.py"],
         "env": {
           "EVERNOTE_TOKEN": "S=你的DeveloperToken",
           "YINXIANG_CHINA": "true",
           "YINXIANG_DEFAULT_NOTEBOOK": "我的笔记"
         }
       }
     }
   }
   ```
   - WorkBuddy：`~/.workbuddy/mcp.json`（Windows：`C:\Users\<你>\.workbuddy\mcp.json`）
   - Trae（macOS）：`~/Library/Application Support/Trae/User/settings/mcp.json`（CN 版路径含 `Trae CN`）；或项目级 `.trae/mcp.json`
   - Trae（Windows）：`%APPDATA%\Trae\User\settings\mcp.json`
3. 按上面「第四步」在**自定义连接器**里信任启用 → 重启 → 用自然语言调用：
   - "列出我的印象笔记笔记本"
   - "在印象笔记里搜'项目复盘'"
   - "把这份分析存进印象笔记，标题'现金流分析'，笔记本'我的笔记'"

四个工具：`yinxiang_list_notebooks` / `yinxiang_search_notes` / `yinxiang_get_note` / `yinxiang_create_note_markdown`。

### B. 批量备份文件夹（独立脚本，无需经过 MCP）

```bash
# 1) 先扫描看计划（dry-run，不需要 Token）
.venv/bin/python yinxiang_backup.py --workspace /路径/到/文件夹 --plan professional
# 2) 执行（同名笔记自动跳过，幂等）
.venv/bin/python yinxiang_backup.py --workspace /路径/到/文件夹 --plan professional --run
# 3) 清掉上次清单重跑
.venv/bin/python yinxiang_backup.py --workspace /路径/到/文件夹 --plan professional --reset --run
```

参数：`--workspace`（默认当前目录）、`--notebook`（覆盖默认笔记本）、`--plan`（账户级别，见上表）、`--include-ext py,rar`（含默认跳过的扩展名）、`--exclude-dir`、`--log`（清单路径）、`--config`（指定 mcp.json 回退读 Token）。

Token 解析顺序：环境变量 `EVERNOTE_TOKEN` → `~/.workbuddy/mcp.json` 的 `yinxiang.env`。

---

## 关键实现要点（避坑）

- **笔记格式分两类，不能混用**：
  - `.md` 正文 → **原生 Markdown 笔记**：必须 `attributes.contentClass='yinxiang.markdown'` + 正文含隐藏 `<center>`（里面是 URL 编码的 markdown 源码）。只有这样才能在客户端切换「编辑/预览」看源码。
  - 其他笔记（附件汇总、图片）→ **超级笔记**：`attributes.contentClass='yinxiang.peso.superNote'`。
- **两个常见误区**：① 普通 ENML = 基础富文本（无源码视图）；② 官方 restful `createNoteFromMCP` 会渲染成超级笔记（`yinxiang.peso.superNote`），**同样没有源码**。要真 Markdown 笔记必须自己构造上述结构（见 `yinxiang_md.py`）。
- **ENML 属性严格校验**：DTD 不允许 `class` / `id`（例如 markdown 代码块生成的 `<code class="language-xx">`），否则服务器报 `errorCode=11` 拒收。`yinxiang_md.py` 已内置属性白名单自动剔除。
- **ENML 是严格 XML**：`&` `<` `>` 必须转义；`<br>`/`<hr>` 等空元素必须自闭合为 `<br/>`。
- **附件必须被正文引用**：资源要在正文用 `<en-media type="MIME" hash="MD5hex"/>` 引用才会显示，否则变成"孤儿资源"被隐藏（客户端只看到文件名列表）。备份脚本已为每个附件生成正确引用，并在跑完后做回读核验（`en-media 数 == 资源数`）。
- **体积**：单条笔记上限随账户级别不同（见上表），脚本按 `--plan` 自动分批；超过单文件上限的文件会跳过并提示。
- **`mcp` 版本**：须 `<2`（v2 把 `FastMCP` 改名 `MCPServer`，会导入报错）。`requirements.txt` 已锁。

## Files in this skill

- `server.py` — MCP server，4 个工具（检索 + 回写）。
- `yinxiang_backup.py` — 批量备份脚本（跨平台、路径可配、账户级别自适应、附件 en-media 引用已修复）。
- `yinxiang_md.py` — 笔记格式构造（原生 Markdown 笔记 / 超级笔记 + ENML 属性白名单归一化）。
- `yinxiang_oauth_token.py` — **可选组件**：浏览器自动化获取 1 年 OAuth token（Playwright；`--profile` 登录态持久化、`--write` 写回 mcp.json、`--token` 手动回写、失败留 `oauth_debug.png` 兜底）。详见上方「skills-oauth 正确获取方式」。
- `requirements.txt` — 主依赖（`mcp<2` / `zibuyu_evernote` / `html2text` / `markdown` / `bleach`）；`yinxiang_oauth_token.py` 的 `playwright` 为可选，单独安装。
- `README.md` — 面向用户的完整安装指南（账户级别 / API Key / 配置位置 / 自定义连接器 / Trae）。
