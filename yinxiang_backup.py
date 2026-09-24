#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
印象笔记（Yinxiang）工作区批量备份 —— 跨平台版
把指定文件夹备份到印象笔记（默认笔记本）：
  - .md 文件：每个独立成一条笔记（相对路径作唯一标题），走印象笔记官方 Markdown 接口存为原生 Markdown 笔记
  - 其他类型：按扩展名分组，组内自动分批，文件作为附件（<en-media> 内嵌引用，可点击下载）
  - 所有图片：合并为一条笔记，内联展示
  - .py / .rar：默认跳过（可用 --include-ext 包含）
  - 排除目录：.workbuddy / .git / __pycache__ / node_modules 等（可用 --exclude-dir 调整）

配置（优先级：命令行 > 环境变量 > ~/.workbuddy/mcp.json）：
  EVERNOTE_TOKEN              Developer Token（必填）
  YINXIANG_DEFAULT_NOTEBOOK   默认笔记本名
  YINXIANG_CHINA             true（国内版）
  YINXIANG_CONFIG            mcp.json 路径（默认 ~/.workbuddy/mcp.json，仅当未设 EVERNOTE_TOKEN 时回退读取）

用法：
  python yinxiang_backup.py --workspace /path/to/folder            # 仅扫描分类，打印计划（dry-run）
  python yinxiang_backup.py --workspace /path/to/folder --run      # 创建笔记（同名已存在则跳过，幂等）
  python yinxiang_backup.py --workspace /path/to/folder --reset --run  # 先删上次清单中的笔记，再干净重跑
  python yinxiang_backup.py --workspace . --notebook 我的笔记本 --include-ext py,rar  # 自定义笔记本 + 含 .py/.rar
依赖：pip install "mcp<2" zibuyu_evernote html2text markdown bleach
"""
import os
import sys
import json
import time
import hashlib
import binascii
import argparse
from xml.sax.saxutils import escape as xescape
from html.parser import HTMLParser
import markdown
import bleach
from zibuyu_evernote import MyEvernote
from zibuyu_evernote.edam.type.ttypes import Note, NoteAttributes, Data, Resource, ResourceAttributes
from zibuyu_evernote.edam.error.ttypes import EDAMSystemException, EDAMErrorCode

# 同目录共享模块：笔记格式构造（.md → 原生 Markdown 笔记；其他 → 超级笔记）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yinxiang_md import build_markdown_note, CONTENT_CLASS_SUPERNOTE  # noqa: E402

HOME = os.path.expanduser("~")
DEFAULT_CONFIG = os.environ.get("YINXIANG_CONFIG") or os.path.join(HOME, ".workbuddy", "mcp.json")
DEFAULT_LOG = os.path.join(HOME, ".workbuddy", "yinxiang_backup_manifest.json")

EXCLUDE_DIRS = {".workbuddy", ".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".trae"}
EXCLUDE_EXT = {".py", ".rar", ".pyc"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
# 账户级别 -> (单条笔记大小上限 MB, 每月上传流量 MB)
# 数据来源：印象笔记帮助中心 https://help.yinxiang.com/hc/articles/63016
PLAN_LIMITS = {
    "free": (25, 60),              # 免费帐户：25MB / 60MB
    "standard": (50, 1024),        # 标准帐户：50MB / 1GB
    "premium": (200, 10240),       # 高级帐户：200MB / 10GB
    "professional": (300, 20480),  # 专业帐户：300MB / 20GB
}
DEFAULT_PLAN = "free"           # 默认按最保守（免费）处理，避免超限失败；付费用户请用 --plan

# 每批笔记体积预算：单条上限留 5MB 余量（正文/元数据开销）
PER_NOTE_BUDGET = max(PLAN_LIMITS[DEFAULT_PLAN][0] - 5, 1) * 1024 * 1024
SINGLE_CAP = PLAN_LIMITS[DEFAULT_PLAN][0] * 1024 * 1024  # 超过即跳过（服务端硬限制）

ALLOWED_TAGS = ["p", "br", "div", "span", "b", "strong", "i", "em", "u", "a", "ul", "ol",
                "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "code",
                "hr", "table", "thead", "tbody", "tr", "th", "td", "colgroup", "col",
                "img", "font", "del", "sub", "sup", "small", "strike"]
ALLOWED_ATTRS = {"a": ["href"], "img": ["src", "alt", "width", "height"],
                 "font": ["color", "face", "size"], "table": ["border"],
                 "col": ["span"], "colgroup": ["span"]}
MD_EXT = ["tables", "fenced_code", "nl2br", "sane_lists"]
VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}


def _attr_str(attrs):
    if not attrs:
        return ""
    parts = []
    for k, v in attrs:
        parts.append('%s="%s"' % (k, xescape(v or "").replace('"', "&quot;")))
    return " " + " ".join(parts)


class _XMLNorm(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []

    def handle_starttag(self, tag, attrs):
        s = "<%s%s/>" % (tag, _attr_str(attrs)) if tag in VOID_TAGS else "<%s%s>" % (tag, _attr_str(attrs))
        self.out.append(s)

    def handle_startendtag(self, tag, attrs):
        self.out.append("<%s%s/>" % (tag, _attr_str(attrs)))

    def handle_endtag(self, tag):
        if tag not in VOID_TAGS:
            self.out.append("</%s>" % tag)

    def handle_data(self, data):
        self.out.append(xescape(data))

    def handle_entityref(self, name):
        self.out.append("&%s;" % name)

    def handle_charref(self, name):
        self.out.append("&#%s;" % name)


def normalize_html(html):
    p = _XMLNorm()
    p.feed(html)
    p.close()
    return "".join(p.out)


MIME = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword", ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel", ".csv": "text/csv",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint", ".txt": "text/plain",
    ".html": "text/html", ".htm": "text/html", ".svg": "image/svg+xml",
    ".json": "application/json", ".md": "text/markdown", ".miora": "application/octet-stream",
}
IMG_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp", ".svg": "image/svg+xml"}
ENML_HEAD = '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd"><en-note>'
ENML_TAIL = "</en-note>"


def md_to_enml(text):
    html = markdown.markdown(text, extensions=MD_EXT)
    clean = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
    try:
        body = normalize_html(clean)
    except Exception:
        body = "<pre>" + xescape(clean) + "</pre>"
    return ENML_HEAD + body + ENML_TAIL


def get_config():
    """返回 (token, default_notebook, china)。优先级：环境变量 > mcp.json。"""
    token = os.environ.get("EVERNOTE_TOKEN") or os.environ.get("YINXIANG_TOKEN")
    nb = os.environ.get("YINXIANG_DEFAULT_NOTEBOOK") or ""
    china = os.environ.get("YINXIANG_CHINA", "true").strip().lower() in ("1", "true", "yes", "y")
    if not token:
        cfg_path = os.environ.get("YINXIANG_CONFIG", DEFAULT_CONFIG)
        if os.path.exists(cfg_path):
            try:
                env = json.load(open(cfg_path, encoding="utf-8"))["mcpServers"]["yinxiang"]["env"]
                token = env.get("EVERNOTE_TOKEN") or env.get("YINXIANG_TOKEN")
                nb = nb or env.get("YINXIANG_DEFAULT_NOTEBOOK", "")
                china = env.get("YINXIANG_CHINA", "true").strip().lower() in ("1", "true", "yes", "y")
            except Exception as e:
                print(f"  [提示] 读取 {cfg_path} 失败：{e}")
    if not token:
        raise SystemExit("ERROR: 未找到 EVERNOTE_TOKEN。请设置环境变量，或在 mcp.json 的 yinxiang.env 中配置后重试。")
    return token, nb, china


def scan(workspace, exclude_dirs, exclude_ext):
    md, images, others, skipped = [], [], {}, []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith(".")]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            full = os.path.join(root, f)
            if ext in exclude_ext:
                skipped.append((full, "排除扩展名"))
                continue
            try:
                sz = os.path.getsize(full)
            except OSError:
                skipped.append((full, "无法读取"))
                continue
            if SINGLE_CAP and sz > SINGLE_CAP:
                skipped.append((full, "超过大小上限"))
                continue
            if ext == ".md":
                md.append((full, sz))
            elif ext in IMAGE_EXT:
                images.append((full, sz))
            else:
                others.setdefault(ext, []).append((full, sz))
    return md, images, others, skipped


def build_resource(file_path, as_attachment=True):
    with open(file_path, "rb") as fh:
        data = fh.read()
    h = hashlib.md5(data).digest()
    r = Resource()
    ext = os.path.splitext(file_path)[1].lower()
    r.mime = MIME.get(ext, "application/octet-stream")
    r.data = Data()
    r.data.size = len(data)
    r.data.bodyHash = h
    r.data.body = data
    r.attributes = ResourceAttributes()
    r.attributes.fileName = os.path.basename(file_path)
    r.attributes.attachment = as_attachment
    return r, h, len(data)


def make_batches(items, budget=PER_NOTE_BUDGET):
    batches, cur, cur_sz = [], [], 0
    for it in items:
        sz = it[1]
        if cur and cur_sz + sz > budget:
            batches.append(cur)
            cur, cur_sz = [], 0
        cur.append(it)
        cur_sz += sz
    if cur:
        batches.append(cur)
    return batches


def get_existing_titles(client, nb_guid):
    from zibuyu_evernote.edam.notestore.ttypes import NoteFilter, NotesMetadataResultSpec
    nf = NoteFilter()
    nf.notebookGuid = nb_guid
    spec = NotesMetadataResultSpec()
    spec.includeTitle = True
    titles, offset = set(), 0
    while True:
        res = client.note_store.findNotesMetadata(client.auth_token, nf, offset, 100, spec)
        for n in (res.notes or []):
            titles.add(n.title)
        if not res.notes or len(res.notes) < 100:
            break
        offset += 100
    return titles


def create_note_with_retry(client, note, attempts=4):
    last = None
    for i in range(attempts):
        try:
            return client.note_store.createNote(note)
        except EDAMSystemException as e:
            last = e
            code = getattr(e, "errorCode", None)
            if code == EDAMErrorCode.RATE_LIMIT_REACHED:
                wait = (getattr(e, "rateLimitDuration", 1) or 1) + 1
            else:
                wait = 2 * (i + 1)
            print(f"    [重试 {i+1}/{attempts}] 限流/瞬时错误，{wait}s 后重试...")
            time.sleep(wait)
    raise last


def create_md_via_native(client, title, md_text, nb_guid):
    """用 EDAM 建「原生 Markdown 笔记」（客户端可切换 编辑/预览、查看源码）。
    说明：官方 restful createNoteFromMCP 会把 markdown 渲染成「超级笔记」
    (contentClass=yinxiang.peso.superNote)，没有源码视图；故改走本方式：
    contentClass='yinxiang.markdown' + 正文隐藏 <center> 内的 URL 编码源码。
    """
    note = build_markdown_note(title, md_text, nb_guid)
    return create_note_with_retry(client, note)


def main():
    ap = argparse.ArgumentParser(description="印象笔记工作区批量备份（跨平台）")
    ap.add_argument("--workspace", default=os.getcwd(), help="要备份的文件夹（默认当前目录）")
    ap.add_argument("--notebook", default=None, help="目标笔记本名（覆盖 YINXIANG_DEFAULT_NOTEBOOK）")
    ap.add_argument("--run", action="store_true", help="真正创建笔记（不加则只做扫描计划）")
    ap.add_argument("--reset", action="store_true", help="先按上次清单删除笔记再重跑")
    ap.add_argument("--log", default=DEFAULT_LOG, help="清单/日志文件路径")
    ap.add_argument("--include-ext", default="", help="额外要包含的扩展名，逗号分隔，如 py,rar")
    ap.add_argument("--exclude-dir", default="", help="额外排除的目录名，逗号分隔")
    ap.add_argument("--config", default=None, help="mcp.json 路径（回退读取 token）")
    ap.add_argument("--plan", default=DEFAULT_PLAN, choices=sorted(PLAN_LIMITS.keys()),
                    help="印象笔记账户级别（决定单条笔记大小上限与分批）：free|standard|premium|professional")
    args = ap.parse_args()

    if args.config:
        os.environ["YINXIANG_CONFIG"] = args.config
    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(workspace):
        raise SystemExit(f"ERROR: 工作区不存在：{workspace}")

    exclude_dirs = set(EXCLUDE_DIRS)
    if args.exclude_dir:
        exclude_dirs |= {d.strip() for d in args.exclude_dir.split(",") if d.strip()}
    exclude_ext = set(EXCLUDE_EXT)
    if args.include_ext:
        exclude_ext -= {("." + e.strip().lstrip(".")) for e in args.include_ext.split(",") if e.strip()}

    target_nb = args.notebook or os.environ.get("YINXIANG_DEFAULT_NOTEBOOK") or ""

    # 按账户级别应用体积上限与分批预算
    cap_mb, flow_mb = PLAN_LIMITS[args.plan]
    globals()["SINGLE_CAP"] = cap_mb * 1024 * 1024
    note_budget = max(cap_mb - 5, 1) * 1024 * 1024
    flow_txt = ("%dGB" % (flow_mb // 1024)) if flow_mb >= 1024 else ("%dMB" % flow_mb)
    print("账户级别：%s（单条笔记上限 %dMB，每月上传流量 %s）" % (args.plan, cap_mb, flow_txt))
    if args.plan == DEFAULT_PLAN and DEFAULT_PLAN == "free":
        print("  提示：未指定 --plan，当前按最保守的「免费帐户」处理；付费帐户请加 --plan 以获得更少、更整洁的笔记。")

    md, images, others, skipped = scan(workspace, exclude_dirs, exclude_ext)
    img_batches = make_batches(images, note_budget)
    other_batches = {ext: make_batches(lst, note_budget) for ext, lst in others.items()}
    total = len(md) + len(img_batches) + sum(len(b) for b in other_batches.values())
    print("=" * 60)
    print("扫描结果（工作区：%s）" % workspace)
    print("  默认笔记本 :", target_nb or "（账户默认）")
    print("  .md 笔记数 :", len(md), "（每文件独立，相对路径作标题）")
    print("  图片批次数 :", len(img_batches), f"（共 {len(images)} 张）")
    for ext in sorted(others):
        print(f"  {ext}: {len(others[ext])} 个 -> {len(other_batches[ext])} 条笔记")
    print("  预计创建笔记总数:", total, "| 跳过:", len(skipped))
    for s, r in skipped:
        print(f"    - [{r}] {os.path.relpath(s, workspace)}")
    print("=" * 60)
    if not args.run:
        print("【DRY-RUN】未创建任何笔记。加 --run 执行；先清半成品用 --reset --run。")
        return

    token, default_nb, china = get_config()
    if not target_nb:
        target_nb = default_nb
    client = MyEvernote(auth_token=token, sandbox=False, china=china)
    notebooks = client.get_all_notebooks()
    target = next((n for n in notebooks if n.name == target_nb), None) if target_nb else None
    if target_nb and not target:
        print(f"ERROR: 默认笔记本「{target_nb}」不存在。可用笔记本：{', '.join(n.name for n in notebooks) or '（无）'}")
        return
    nb_guid = target.guid if target else ""
    print(f"目标笔记本: {target.name if target else '账户默认'} ({nb_guid[:12]}...)\n" if nb_guid else "目标笔记本: 账户默认\n")

    if args.reset and os.path.exists(args.log):
        try:
            old = json.load(open(args.log, "r", encoding="utf-8"))
            guids = [n["guid"] for n in old.get("notes", [])]
            print(f"--reset：删除上次 {len(guids)} 条笔记...")
            ok = 0
            for g in guids:
                try:
                    client.note_store.deleteNote(g)
                    ok += 1
                except Exception:
                    pass
            print(f"    已删除 {ok}/{len(guids)}")
        except Exception as e:
            print("reset 读取清单失败，跳过：", e)

    existing = get_existing_titles(client, nb_guid) if nb_guid else set()
    print(f"已存在同名笔记 {len(existing)} 条（将跳过）\n")

    manifest, created, skipped_exist, failed = [], 0, 0, []

    def maybe_create(note, kind, files):
        nonlocal created, skipped_exist
        if nb_guid and note.title in existing:
            skipped_exist += 1
            return None
        res = create_note_with_retry(client, note)
        created += 1
        manifest.append({"title": note.title, "guid": res.guid, "type": kind, "files": files})
        return res

    for fp, _ in md:
        rel = os.path.relpath(fp, workspace).replace(os.sep, "/")
        title = os.path.splitext(rel)[0]
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
            if nb_guid and title in existing:
                skipped_exist += 1
                print(f"  [md] 跳过（已存在）《{title}》")
                continue
            res = create_md_via_native(client, title, text, nb_guid)
            created += 1
            manifest.append({"title": title, "guid": res.guid, "type": "md", "files": [os.path.basename(fp)]})
            print(f"  [md] 已创建原生 Markdown 笔记《{title}》")
            time.sleep(0.12)
        except Exception as e:
            failed.append((fp, f"{type(e).__name__}: {e}"))
            print(f"  [md] 失败 {fp}: {e}")

    for i, batch in enumerate(img_batches, 1):
        try:
            resources, body = [], ENML_HEAD + f"<div>本笔记备份以下 {len(batch)} 张图片：</div>"
            for fp, _ in batch:
                r, h, _ = build_resource(fp, as_attachment=True)
                ext = os.path.splitext(fp)[1].lower()
                r.mime = IMG_MIME.get(ext, "image/png")
                resources.append(r)
                body += f'<div><en-media type="{r.mime}" hash="{binascii.hexlify(h).decode()}"/></div>'
            body += ENML_TAIL
            title = f"图片备份（第{i}批，共{len(img_batches)}批）" if len(img_batches) > 1 else f"图片备份（全部{len(batch)}张）"
            note = Note()
            note.title = title
            note.content = body
            note.resources = resources
            note.notebookGuid = nb_guid
            note.attributes = NoteAttributes()
            note.attributes.contentClass = CONTENT_CLASS_SUPERNOTE
            if maybe_create(note, "image", [os.path.basename(p) for p, _ in batch]):
                print(f"  [图片] 已创建《{title}》（{len(batch)} 张）")
            time.sleep(0.15)
        except Exception as e:
            failed.append(("图片批%d" % i, f"{type(e).__name__}: {e}"))
            print(f"  [图片] 失败: {e}")

    for ext in sorted(others):
        for i, batch in enumerate(other_batches[ext], 1):
            try:
                resources, body = [], ENML_HEAD + f"<div>本笔记备份以下 {len(batch)} 个 {ext} 文件，已作为附件附在笔记中（点击即可下载）：</div><ul>"
                for fp, _ in batch:
                    r, h, _ = build_resource(fp, as_attachment=True)
                    resources.append(r)
                    hexh = binascii.hexlify(h).decode()
                    fn = xescape(os.path.basename(fp))
                    # 关键：必须用 en-media 引用资源，否则 Evernote 不显示（孤儿资源被隐藏）
                    body += f'<li><en-media type="{r.mime}" hash="{hexh}"/> {fn}</li>'
                body += "</ul>" + ENML_TAIL
                title = f"文件备份 - {ext}（第{i}批，共{len(other_batches[ext])}批）" if len(other_batches[ext]) > 1 else f"文件备份 - {ext}（{len(batch)}个）"
                note = Note()
                note.title = title
                note.content = body
                note.resources = resources
                note.notebookGuid = nb_guid
                note.attributes = NoteAttributes()
                note.attributes.contentClass = CONTENT_CLASS_SUPERNOTE
                if maybe_create(note, f"att-{ext}", [os.path.basename(p) for p, _ in batch]):
                    print(f"  [附件 {ext}] 已创建《{title}》（{len(batch)} 个，已内嵌附件）")
                time.sleep(0.15)
            except Exception as e:
                failed.append((f"{ext}批{i}", f"{type(e).__name__}: {e}"))
                print(f"  [附件 {ext}] 失败: {e}")

    # 验证：重建的附件笔记必须 en-media 数 == 资源数（否则文件仍是孤儿资源、不显示）
    print("\n=== 附件笔记 en-media 引用核验 ===")
    bad = []
    for rec in manifest:
        if not rec["type"].startswith("att-"):
            continue
        try:
            n = client.note_store.getNote(rec["guid"], True, True, False, False)
            rc = len(n.resources or [])
            mc = (n.content or "").count("<en-media")
            status = "OK" if mc == rc and rc > 0 else "BAD"
            if status == "BAD":
                bad.append((rec["title"], rc, mc))
            print(f"  [{status}] 《{rec['title']}》 资源={rc} en-media={mc}")
        except Exception as e:
            bad.append((rec["title"], -1, -1))
            print(f"  [ERR] 《{rec['title']}》: {e}")
    if bad:
        print(f"  !! {len(bad)} 条附件笔记引用异常，需排查。")
    else:
        print("  全部附件笔记引用正常 ✓")

    with open(args.log, "w", encoding="utf-8") as fh:
        json.dump({"notebook": target.name if target else "默认", "note_count": created, "notes": manifest},
                  fh, ensure_ascii=False, indent=2)
    print(f"\n完成：新建 {created} 条，跳过已存在 {skipped_exist} 条，失败 {len(failed)} 条。目标笔记本「{target.name if target else '默认'}」")
    if failed:
        print("失败清单：")
        for f, e in failed:
            print(f"  - {f}: {e}")
    print("清单见", args.log)


if __name__ == "__main__":
    main()
