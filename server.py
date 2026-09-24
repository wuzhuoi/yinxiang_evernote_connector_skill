#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
印象笔记 (Yinxiang / Evernote China) MCP Server —— 跨平台版
功能：检索笔记（read）+ 将 Markdown 回写为笔记（write）
配置（环境变量，均可在 WorkBuddy / Trae 的 mcp.json 的 env 中设置）：
  EVERNOTE_TOKEN             印象笔记 Developer Token（必填，只显示一次）
  YINXIANG_DEFAULT_NOTEBOOK  默认回写笔记本名称（可选；也可每次调用用 notebook_name 指定）
  YINXIANG_CHINA             固定 true（国内版 app.yinxiang.com）
依赖：pip install "mcp<2" zibuyu_evernote html2text markdown
"""
import os
import re
import sys
import html2text
from mcp.server.fastmcp import FastMCP
from zibuyu_evernote import MyEvernote
from zibuyu_evernote.edam.notestore.ttypes import NoteFilter, NotesMetadataResultSpec

# 让 `python server.py` 能 import 同目录的共享模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yinxiang_md import create_markdown_note as create_native_markdown_note  # noqa: E402

TOKEN = (os.environ.get("EVERNOTE_TOKEN") or os.environ.get("YINXIANG_TOKEN") or "").strip()
CHINA = os.environ.get("YINXIANG_CHINA", "true").strip().lower() in ("1", "true", "yes", "y")
DEFAULT_NOTEBOOK = (os.environ.get("YINXIANG_DEFAULT_NOTEBOOK") or "").strip()

mcp = FastMCP("yinxiang")


def get_client():
    if not TOKEN:
        raise RuntimeError(
            "缺少环境变量 EVERNOTE_TOKEN。请在 mcp.json（WorkBuddy/Trae）的 yinxiang 连接器 env 中配置印象笔记 Developer Token。"
        )
    return MyEvernote(auth_token=TOKEN, sandbox=False, china=CHINA)


def resolve_notebook_guid(client, notebook_name):
    """笔记本名称 -> GUID；空字符串表示使用账户默认笔记本。"""
    if not notebook_name:
        return ""
    notebooks = client.get_all_notebooks()
    for nb in notebooks:
        if nb.name == notebook_name:
            return nb.guid
    for nb in notebooks:
        if nb.name.lower() == notebook_name.lower():
            return nb.guid
    avail = ", ".join(nb.name for nb in notebooks) or "（无）"
    raise ValueError(f"未找到笔记本“{notebook_name}”。可用笔记本：{avail}")


def enml_to_markdown(content):
    if not content:
        return ""
    body = re.sub(r"<\?xml.*?\?>", "", content, flags=re.S)
    body = re.sub(r"<!DOCTYPE.*?>", "", body, flags=re.S)
    return html2text.html2text(body).strip()


@mcp.tool()
def yinxiang_list_notebooks() -> str:
    """列出印象笔记中所有笔记本（名称 + GUID），用于确认可写入的目标笔记本。"""
    client = get_client()
    notebooks = client.get_all_notebooks()
    if not notebooks:
        return "（账户中暂无笔记本）"
    return "\n".join(f"- {nb.name}  (guid: {nb.guid})" for nb in notebooks)


@mcp.tool()
def yinxiang_search_notes(query: str, notebook_name: str = "", max_results: int = 10) -> str:
    """按关键词检索印象笔记，返回标题与 GUID。
    query: 搜索关键词（必填）
    notebook_name: 可选，限定在某笔记本内搜索；留空则全账户搜索
    max_results: 返回条数上限（默认 10）
    """
    import datetime
    client = get_client()
    nf = NoteFilter()
    nf.words = query
    if notebook_name:
        nf.notebookGuid = resolve_notebook_guid(client, notebook_name)
    spec = NotesMetadataResultSpec()
    spec.includeTitle = True
    spec.includeUpdated = True
    spec.includeNotebookGuid = True
    spec.includeTagGuids = False
    spec.includeAttributes = False
    result = client.note_store.findNotesMetadata(client.auth_token, nf, 0, max_results, spec)
    notes = result.notes or []
    if not notes:
        return f"未找到匹配“{query}”的笔记。"
    lines = []
    for n in notes:
        updated = ""
        if getattr(n, "updated", None):
            try:
                updated = " | 更新:" + datetime.datetime.fromtimestamp(n.updated / 1000).strftime("%Y-%m-%d")
            except Exception:
                pass
        lines.append(f"- {n.title}  (guid: {n.guid}){updated}")
    return "\n".join(lines)


@mcp.tool()
def yinxiang_get_note(guid: str) -> str:
    """获取某条笔记的正文，转换为 Markdown 返回。
    guid: 笔记 GUID（来自检索结果）
    """
    client = get_client()
    note = client.get_note(guid, with_content=True, with_resources_data=False)
    md = enml_to_markdown(getattr(note, "content", ""))
    title = getattr(note, "title", "(无标题)")
    return f"# {title}\n\n{md}"


@mcp.tool()
def yinxiang_create_note_markdown(title: str, markdown_content: str, notebook_name: str = "") -> str:
    """将一段 Markdown 回写为印象笔记「原生 Markdown 笔记」（客户端可切换 编辑/预览、查看源码）。
    title: 笔记标题（必填）
    markdown_content: Markdown 正文（必填）
    notebook_name: 可选，写入的笔记本名称；留空则用环境变量 YINXIANG_DEFAULT_NOTEBOOK，
                   若仍未设置则写入账户默认笔记本。
    """
    client = get_client()
    nb_name = (notebook_name or DEFAULT_NOTEBOOK).strip()
    nb_guid = resolve_notebook_guid(client, nb_name) if nb_name else ""
    note = create_native_markdown_note(client, title, markdown_content, nb_guid)
    actual = ""
    try:
        actual = client.get_notebook(nb_guid).name if nb_guid else client.get_default_notebook().name
    except Exception:
        pass
    return f"已创建原生 Markdown 笔记：《{note.title}》(guid: {note.guid})，写入笔记本：{actual or '默认'}"


if __name__ == "__main__":
    mcp.run()
