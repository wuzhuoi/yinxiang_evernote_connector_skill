#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""印象笔记 OAuth Token 获取助手（浏览器自动化，可选组件）

用途：把「7 天失效的 Developer Token」换成印象笔记 skills-oauth 页面换发的
**正式 OAuth access token（默认约 1 年有效）**，解决 token 每 7 天失效的问题。

原理（实测 2026-09-30）：
    1. 打开 https://app.yinxiang.com/third/skills-oauth/（需登录 + 付费会员）；
    2. 点页面上的 **「生成新 Token」按钮**（`#generate-token-btn`）；
       —— 注意：点其它「授权」链接会跳到授权管理页，拿不到 token。
    3. 跳转 `/third/skills-oauth/auth/yinxiang-ai-skill` →
       `/third/mcp-oauth/callback?...` 换发 access token 并显示在页面上；
    4. 本脚本扫描**整页内容**（含 URL 解码后）提取 token。

依赖（本包 requirements.txt 之外，按需单独安装）：
    pip install playwright && playwright install chromium
    可选：不装也能跑，只需把 executable_path 指向本机已有的 Chrome/Chromium。

用法：
    python yinxiang_oauth_token.py --profile /path/to/profile        # 首次：会弹窗让你登录
    python yinxiang_oauth_token.py --profile /path/to/profile --write  # 抓到后写入 mcp.json
    python yinxiang_oauth_token.py --chrome "C:/path/chrome.exe" --profile /path/to/profile

说明：
    - `--profile` 是一个**独立的**浏览器档案目录（不要指向你正在使用的 Chrome 档案，
      否则会被占用/损坏）。登录态会保存在该目录，下次可直接复用。
    - 若自动抓取失败，脚本会保留截图与页面文本，你也可以**手动从页面上复制** token
      （形如 S=s45:U=...:A=yinxiang-skill:...），再用 --token 参数写入。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse

OAUTH_URL = "https://app.yinxiang.com/third/skills-oauth/"

# 严格匹配 EDAM/OAuth token：S=s<shard>:U=<uid>:E=<exp>:...:A=<app>:V=2:H=<hash32>
STRICT = re.compile(r"S=s\d+:U=[0-9a-fA-F]+:E=[0-9a-fA-F]+:[A-Za-z0-9:=]+")

DEFAULT_MCP = os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")


def P(*a):
    print(*a, flush=True)


def find_token(text):
    """在文本中找 token；会先尝试 URL 解码（token 在页面里可能是百分号编码的）。"""
    if not text:
        return None
    for cand in (text, urllib.parse.unquote(text)):
        m = STRICT.search(cand)
        if m:
            return m.group(0)
    return None


def write_mcp(token, mcp_path):
    with open(mcp_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["mcpServers"]["yinxiang"]["env"]["EVERNOTE_TOKEN"] = token
    with open(mcp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    P("已写入:", mcp_path)


def main():
    ap = argparse.ArgumentParser(description="印象笔记 OAuth Token 获取助手（浏览器自动化）")
    ap.add_argument("--profile", required=True, help="独立浏览器档案目录（保存登录态，勿指向正在使用的 Chrome 档案）")
    ap.add_argument("--chrome", default=None, help="Chrome/Chromium 可执行文件路径；留空用 playwright 自带 chromium")
    ap.add_argument("--token", default=None, help="跳过浏览器：直接把这段 token 写入 mcp.json")
    ap.add_argument("--mcp", default=DEFAULT_MCP, help="mcp.json 路径（默认 ~/.workbuddy/mcp.json）")
    ap.add_argument("--write", action="store_true", help="抓到 token 后写入 mcp.json")
    ap.add_argument("--wait-login", type=int, default=300, help="等待用户登录的最长秒数（默认 300）")
    args = ap.parse_args()

    if args.token:
        t = find_token(args.token) or args.token
        if not t.startswith("S="):
            P("ERROR: 给定的 token 格式不像印象笔记 token（应以 S= 开头）"); sys.exit(1)
        P("TOKEN:", t)
        write_mcp(t, args.mcp)
        P("DONE")
        return

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        P("ERROR: 缺少 playwright。请先：pip install playwright && playwright install chromium")
        sys.exit(1)

    token = None
    with sync_playwright() as p:
        kw = dict(user_data_dir=args.profile, headless=False,
                  args=["--no-first-run", "--no-default-browser-check", "--start-maximized"])
        if args.chrome:
            kw["executable_path"] = args.chrome
        ctx = p.chromium.launch_persistent_context(**kw)
        page = ctx.new_page()
        page.goto(OAUTH_URL, wait_until="networkidle", timeout=45000)

        # 1) 未登录则等用户登录
        deadline = time.time() + args.wait_login
        while "Login.action" in (page.url or "") and time.time() < deadline:
            P("请在弹出的浏览器窗口登录印象笔记（脚本会自动继续）... url=", page.url)
            page.wait_for_timeout(5000)
            if "Login.action" not in (page.url or ""):
                break
        if "Login.action" in (page.url or ""):
            P("ERROR: 登录等待超时。请重跑并先在窗口里完成登录。")
            ctx.close(); sys.exit(2)

        P("页面就绪:", page.url)

        # 2) 点「生成新 Token」
        if page.locator("#generate-token-btn").count():
            P("点击 #generate-token-btn（生成新 Token）")
            page.click("#generate-token-btn")
        else:
            P("ERROR: 未找到 #generate-token-btn。页面可能需要付费会员权限，或结构已变化。")
            P("页面文本片段:", (page.inner_text("body") or "")[:500])
            try:
                page.screenshot(path="oauth_debug.png")
                P("已保存截图 oauth_debug.png")
            except Exception:
                pass
            ctx.close(); sys.exit(3)

        # 3) 轮询整页内容抓取 token（比只查 #token-code 更稳）
        deadline = time.time() + 40
        while time.time() < deadline:
            for src, txt in (("url", page.url), ("dom", page.content())):
                t = find_token(txt)
                if t:
                    token = t; P("TOKEN_FROM_" + src.upper()); break
            if token:
                break
            try:
                code = page.locator("#token-code").inner_text(timeout=1200)
                t = find_token(code)
                if t:
                    token = t; P("TOKEN_FROM_DOM_ELEMENT"); break
            except Exception:
                pass
            page.wait_for_timeout(1500)

        if not token:
            P("未自动抓到 token。当前 url=", page.url)
            try:
                page.screenshot(path="oauth_debug.png")
                P("已保存截图 oauth_debug.png —— 你也可以直接从页面上复制 token，然后用：")
                P("  python yinxiang_oauth_token.py --token 'S=s45:...' --write")
            except Exception:
                pass
        ctx.close()

    if not token:
        P("FAILED"); sys.exit(4)

    P("TOKEN_FOUND:", token)
    if args.write:
        write_mcp(token, args.mcp)
    else:
        P("（未加 --write，未写入 mcp.json）")
    P("DONE")


if __name__ == "__main__":
    main()
