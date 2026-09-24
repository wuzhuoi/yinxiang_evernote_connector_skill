# 印象笔记（Yinxiang）连接器 · 跨平台能力包

把你的印象笔记变成 AI 客户端的"读写外脑"：既能**检索**已有笔记，又能把产出**回写为原生 Markdown 笔记**（客户端可切源码/预览），还能把整个文件夹**批量备份**成笔记（`.md` 独立成篇、其他文件作附件、图片内联）。

- 国内版 `app.yinxiang.com`，基于 `zibuyu_evernote`（Python 3 版 Evernote EDAM SDK）+ FastMCP。
- **Windows / macOS** 通吃，**WorkBuddy / Trae / 任意支持 stdio MCP 的客户端**通吃。

---

## 第一步：确认你的账户级别

印象笔记不同账户的**单条笔记大小上限**和**每月上传流量**不同，这会直接影响备份脚本的分批方式。请先确认自己属于哪一档：

| 账户级别 | 备份脚本参数 `--plan` | 单条笔记大小上限 | 每月上传流量 |
|---|---|---|---|
| 免费帐户 | `free` | 25 MB | 60 MB |
| 标准帐户 | `standard` | 50 MB | 1 GB |
| 高级帐户 | `premium` | 200 MB | 10 GB |
| 专业帐户 | `professional` | 300 MB | 20 GB |

> 数据来源：印象笔记帮助中心「我应该选用哪种印象笔记帐户类型?」（https://help.yinxiang.com/hc/articles/63016）。若官网后续调整，以官网为准。
> 不知道怎么查：打开印象笔记客户端 → 账户/设置 → 查看当前会员等级。备份脚本**默认按最保守的 `free` 处理**，付费用户建议显式加 `--plan`。

---

## 第二步：生成 API Key（Developer Token）

印象笔记用 **Developer Token** 做第三方认证（国内版）：

1. 浏览器登录网页版：**https://app.yinxiang.com**
2. **手动访问下面这个地址**（它不在任何菜单里，需要直接在地址栏输入）：
   ```
   https://app.yinxiang.com/api/DeveloperToken.action
   ```
3. 点击页面上的 **"Create a developer token"** 按钮。
4. 页面会生成一串以 **`S=`** 开头的长字符串，例如
   `S=s0:U=1234567:E=0123456789a:C=0123456789b:P=1cd:A=en-devtoken:V=2:H=00000000000000000000000000000000`。
   复制并妥善保存——⚠️ **它只显示一次**。
5. 如果上面的页面打不开或找不到入口，改用印象笔记为 AI 对接提供的授权页：
   ```
   https://app.yinxiang.com/third/skills-oauth/
   ```
   登录后点授权，同样可拿到 Token。

> 🔒 **安全提醒**：Developer Token 等同于你整个印象笔记账户的钥匙，谁拿到谁就能读写。**不要**提交到 Git 仓库、不要放进公开同步盘、不要发到群里。若怀疑泄露，回到上面的页面撤销并重新生成。

---

## 第三步：准备 Python 环境

需要 **Python 3.10+**。推荐建独立 venv（避免污染系统环境）：

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

记下 venv 里 python 可执行文件的**绝对路径**，第四步要用：
- macOS：`<包目录>/.venv/bin/python`
- Windows：`<包目录>\.venv\Scripts\python.exe`

---

## 第四步：把 API Key 放进配置文件

编辑（不存在就新建）你客户端的主配置文件 `mcp.json`，加入 `yinxiang` 这一段：

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

说明：
- `command` 用 venv 里的 python（这样 `mcp` 等依赖才找得到）。
- `args` 指向本包的 `server.py` 绝对路径。
- `EVERNOTE_TOKEN` 填第二步拿到的 Token。
- `YINXIANG_DEFAULT_NOTEBOOK` 是默认写入的笔记本名（可留空 = 账户默认笔记本）。调用工具时也可用 `notebook_name` 参数临时指定，优先级更高。

配置文件位置：
- **WorkBuddy**：`~/.workbuddy/mcp.json`（Windows：`C:\Users\<你>\.workbuddy\mcp.json`）
- **Trae（macOS）**：`~/Library/Application Support/Trae/User/settings/mcp.json`（CN 版路径含 `Trae CN`）
- **Trae（Windows）**：`%APPDATA%\Trae\User\settings\mcp.json`

---

## 第五步：在 WorkBuddy 里连接（关键按钮）

1. 保存好 `mcp.json` 后，**重启 WorkBuddy**。
2. 打开**连接器管理**页面。
3. 点击页面**右上角的「自定义连接器」**。
4. 在列表里找到 **`yinxiang`**，点 **信任 / 启用**。
5. 新开一个会话，直接说：**"列出我的印象笔记笔记本"**——能返回笔记本列表就说明接入成功。

之后就可用自然语言读写印象笔记了：
- "在印象笔记里搜'种植贷风控'"
- "把这份分析存进印象笔记，标题'现金流分析'，笔记本'我的笔记'"

---

## 第六步：在 Trae 里连接

Trae 同样支持 stdio MCP，配置字段与上面完全一致（`command` + `args` + `env`）。

1. 打开 Trae **设置 → MCP → 添加 → 手动添加**；
2. 把第四步那段 JSON 粘进输入框，确认；
3. 重启 Trae，即可调用 `yinxiang_*` 四个工具。
4. 也可以放到**项目级** `.trae/mcp.json`（跟项目走，适合多项目隔离）。

> 批量备份脚本 `yinxiang_backup.py` 是独立 Python 脚本，在 Trae 的终端里直接跑即可，无需经过 MCP。

---

## 四个 MCP 工具

| 工具 | 作用 | 示例指令 |
|---|---|---|
| `yinxiang_list_notebooks` | 列出所有笔记本（名称+GUID） | "列出我的印象笔记笔记本" |
| `yinxiang_search_notes` | 关键词检索，返回标题+GUID | "在印象笔记里搜'项目复盘'" |
| `yinxiang_get_note` | 取某条笔记正文（转 Markdown） | "打开 guid 为 xxx 的那条笔记" |
| `yinxiang_create_note_markdown` | 把 Markdown 回写为**原生 Markdown 笔记**（可切源码/预览） | "把这份分析存进印象笔记" |

---

## 批量备份文件夹

```bash
# 1) 先扫描看计划（dry-run，不需要 Token）
.venv/bin/python yinxiang_backup.py --workspace /路径/到/文件夹 --plan professional

# 2) 执行（同名笔记自动跳过，幂等）
.venv/bin/python yinxiang_backup.py --workspace /路径/到/文件夹 --plan professional --run

# 3) 清掉上次清单重跑
.venv/bin/python yinxiang_backup.py --workspace /路径/到/文件夹 --plan professional --reset --run
```

| 参数 | 说明 | 默认 |
|---|---|---|
| `--workspace` | 要备份的文件夹 | 当前目录 |
| `--notebook` | 目标笔记本名（覆盖环境变量） | 环境变量 / 账户默认 |
| `--plan` | 账户级别：`free` / `standard` / `premium` / `professional` | `free` |
| `--run` | 真正创建（不加只扫描） | 关闭 |
| `--reset` | 先删上次清单中的笔记再重跑 | 关闭 |
| `--log` | 清单/日志文件路径 | `~/.workbuddy/yinxiang_backup_manifest.json` |
| `--include-ext` | 额外包含扩展名，逗号分隔（如 `py,rar`） | 无 |
| `--exclude-dir` | 额外排除目录，逗号分隔 | 无 |
| `--config` | 指定 mcp.json 路径（回退读 Token） | `~/.workbuddy/mcp.json` |

**备份规则**：
- `.md` → 每文件独立成一条**原生 Markdown 笔记**（客户端可查看/编辑源码，相对路径作标题）；
- 其他文件 → 按扩展名分组、组内按账户体积上限自动分批，文件作为**附件**贴在一条**「超级笔记」**里（可点击下载）；
- 图片 → 合并为一条内联笔记；
- `.py` / `.rar` 默认跳过；`.git` / `.venv` 等隐藏目录自动排除。

---

## 常见问题

**Q：笔记写进去了，但附件只有一个文件名列表、点不开？**
A：附件必须被正文用 `<en-media>` 引用才会显示。本包已修复并内置回读核验（跑完会打印 `资源=N en-media=N`），正常情况不会出现。

**Q：客户端里找不到 Markdown 源码视图？**
A：`.md` 备份用的是**原生 Markdown 笔记**（`contentClass=yinxiang.markdown`，源码藏在正文隐藏的 `<center>` 里），在客户端里可切换「编辑 / 预览」。如果看不到，说明该条不是这种格式——重新用本包脚本备份一次即可。

**Q：上传报错 `errorCode=11`？**
A：多为 ENML 属性不合法（如 `class` / `id`）或 XML 未转义。本包已内置属性白名单与转义处理。

**Q：报 429 / 限流？**
A：印象笔记对 API 调用频次有限制，脚本已带指数退避重试；间隔太短可适当放慢。

**Q：能免费账户用吗？**
A：可以，但单条笔记仅 25MB、每月上传流量 60MB。脚本默认即按 `free` 保守处理。

**Q：Token 放哪里最安全？**
A：放 `mcp.json` 的 `env` 或系统环境变量即可，**不要**放进被同步/被提交的目录。
