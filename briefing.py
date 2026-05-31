#!/usr/bin/env python3
"""
晨间简报 · GitHub Actions 版
生成精美 HTML 页面（jsDelivr CDN 大陆秒开）+ 企业微信链接推送
周一至周五：AI 产业动态 + 软件工程就业
周五附加：本周学术论文 & 保研特辑
"""

import os, re, json, subprocess, html as html_mod, hashlib, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
from openai import OpenAI

# ── 配置 ──

def load_config():
    return {
        "wechat_webhook": os.environ.get("WECHAT_WEBHOOK_URL", ""),
        "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
        "openai_base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "openai_model": os.environ.get("OPENAI_MODEL", "deepseek-v4-pro"),
    }

# ── 源 ──

DAILY_SOURCES = [
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage?count=15", "cat": "ai"},
    {"name": "V2EX 热帖", "url": "https://www.v2ex.com/feed/tab/hot.xml", "cat": "job"},
    {"name": "36氪", "url": "https://36kr.com/feed", "cat": "job"},
    {"name": "Google News AI", "url": "https://news.google.com/rss/search?q=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+AI+%E5%A4%A7%E6%A8%A1%E5%9E%8B+when:24h&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "cat": "ai"},
    {"name": "Google News 就业", "url": "https://news.google.com/rss/search?q=%E4%BA%92%E8%81%94%E7%BD%91+%E6%8B%9B%E8%81%98+%E8%A3%81%E5%91%98+%E9%9D%A2%E7%BB%8F+%E7%A8%8B%E5%BA%8F%E5%91%98+%E5%B0%B1%E4%B8%9A&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "cat": "job"},
]

FRIDAY_SOURCES = [
    {"name": "ArXiv cs.AI", "url": "http://export.arxiv.org/rss/cs.AI", "cat": "academic"},
    {"name": "ArXiv cs.CL", "url": "http://export.arxiv.org/rss/cs.CL", "cat": "academic"},
    {"name": "Reddit ML", "url": "https://www.reddit.com/r/MachineLearning/.rss", "cat": "academic"},
    {"name": "保研 知乎", "url": "https://news.google.com/rss/search?q=%E4%BF%9D%E7%A0%94+%E5%A4%8F%E4%BB%A4%E8%90%A5+%E6%8E%A8%E5%85%8D+site:zhihu.com&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "cat": "baoyan"},
    {"name": "保研 综合", "url": "https://news.google.com/rss/search?q=%E4%BF%9D%E7%A0%94+%E5%A4%8F%E4%BB%A4%E8%90%A5+%E6%8E%A8%E5%85%8D+%E7%A0%94%E7%A9%B6%E7%94%9F%E6%8B%9B%E7%94%9F&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "cat": "baoyan"},
    {"name": "校招 实习", "url": "https://news.google.com/rss/search?q=%E8%BD%AF%E4%BB%B6%E5%B7%A5%E7%A8%8B+%E8%AE%A1%E7%AE%97%E6%9C%BA+%E6%A0%A1%E6%8B%9B+%E5%AE%9E%E4%B9%A0&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "cat": "baoyan"},
    {"name": "南理工研招", "url": "https://news.google.com/rss/search?q=site:njust.edu.cn+%E7%A0%94%E7%A9%B6%E7%94%9F+%E6%8E%A8%E5%85%8D+%E5%A4%8F%E4%BB%A4%E8%90%A5&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "cat": "baoyan"},
    {"name": "南航研招", "url": "https://news.google.com/rss/search?q=site:nuaa.edu.cn+%E7%A0%94%E7%A9%B6%E7%94%9F+%E6%8E%A8%E5%85%8D+%E5%A4%8F%E4%BB%A4%E8%90%A5&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "cat": "baoyan"},
    {"name": "华东211研招", "url": "https://news.google.com/rss/search?q=%E5%A4%8F%E4%BB%A4%E8%90%A5+%E6%8E%A8%E5%85%8D+%E8%AE%A1%E7%AE%97%E6%9C%BA&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "cat": "baoyan"},
]

CDN_BASE = "https://kalditeen.github.io/morning_brief"


def fetch_news(sources, hours_back):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)
    all_items, seen = [], set()
    for src in sources:
        try:
            feed = feedparser.parse(src["url"])
        except Exception as e:
            print(f"  ⚠️ {src['name']}: {e}")
            continue
        cnt = 0
        for e in feed.entries:
            pt = None
            if hasattr(e, "published_parsed") and e.published_parsed:
                pt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(e, "updated_parsed") and e.updated_parsed:
                pt = datetime(*e.updated_parsed[:6], tzinfo=timezone.utc)
            if pt and pt < cutoff:
                continue
            t = html_mod.unescape(getattr(e, "title", "").strip())
            if len(t) < 6:
                continue
            hh = hashlib.md5((src["cat"] + t).encode()).hexdigest()
            if hh in seen:
                continue
            seen.add(hh)
            s = html_mod.unescape(getattr(e, "summary", "") or getattr(e, "description", ""))
            s = re.sub(r"<[^>]+>", "", s)[:400]
            ha = round((now - pt).total_seconds() / 3600, 1) if pt else None
            all_items.append({"title": t, "source": src["name"], "cat": src["cat"], "hours_ago": ha, "summary": s.strip()})
            cnt += 1
        print(f"  ✅ {src['name']}: {cnt} 条")
    all_items.sort(key=lambda x: -(x["hours_ago"] or 0), reverse=True)
    return all_items


def md2html(md: str, date_str: str, title: str) -> str:
    """WeChat markdown → 精美 HTML 页面"""
    # 简单 markdown → HTML 转换
    lines = md.split("\n")
    html_lines = []
    in_list = False
    for line in lines:
        # 标题
        if line.startswith("# ") or line.startswith("## ") or line.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            level = len(line.split(" ")[0])
            text = line[level + 1:]
            sizes = {1: "1.5em", 2: "1.2em", 3: "1.05em"}
            html_lines.append(f'<h{level} style="font-size:{sizes.get(level,"1em")};">{html_mod.escape(text)}</h{level}>')
        elif line.startswith("· ") or line.startswith("- "):
            if not in_list:
                html_lines.append('<ul style="padding-left:1.2em;">')
                in_list = True
            text = line[2:]
            # 处理 → 和 **
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            text = re.sub(r"→", "<span style='color:#764ba2;'>→</span>", text)
            html_lines.append(f"<li>{text}</li>")
        elif line.startswith("> "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line[2:])
            html_lines.append(f'<blockquote style="border-left:3px solid #667eea;padding:8px 16px;margin:12px 0;background:#f0f0ff;border-radius:0 8px 8px 0;">{text}</blockquote>')
        elif line == "===":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append('<hr style="border:none;border-top:1px solid #e0e0e0;margin:20px 0;">')
        elif line.strip():
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            html_lines.append(f"<p>{text}</p>")

    if in_list:
        html_lines.append("</ul>")

    body = "\n".join(html_lines)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta property="og:title" content="📰 晨间简报 | {date_str}">
<meta property="og:description" content="{title}">
<meta property="og:type" content="article">
<title>晨间简报 | {date_str}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;max-width:680px;margin:0 auto;padding:24px 16px 40px;background:#f0f2f5;color:#1a1a2e;line-height:1.8}}
.header{{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;padding:28px 24px;border-radius:14px;text-align:center;margin-bottom:20px;box-shadow:0 4px 16px rgba(102,126,234,0.25)}}
.header h1{{font-size:1.4em;font-weight:700}}
.header p{{font-size:0.85em;opacity:0.85;margin-top:4px}}
.section{{background:#fff;border-radius:12px;padding:20px 22px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,0.04)}}
h2{{font-size:1.1em;margin-bottom:10px;padding-bottom:8px;border-bottom:2px solid #667eea20}}
h3{{font-size:1em}}
p{{margin:6px 0;font-size:0.94em}}
li{{margin:8px 0;font-size:0.93em}}
blockquote{{border-left:3px solid #667eea;padding:8px 16px;margin:12px 0;background:#f8f9ff;border-radius:0 8px 8px 0}}
.footer{{text-align:center;color:#999;font-size:0.78em;margin-top:24px;padding-top:16px;border-top:1px solid #e0e0e0}}
.footer a{{color:#667eea}}
@media(max-width:480px){{body{{padding:12px 8px 30px}}.header{{padding:20px 16px}}.section{{padding:14px 16px}}}}
</style>
</head>
<body>
<div class="header">
<h1>📰 晨间简报</h1>
<p>{date_str}</p>
</div>
{body}
<div class="footer">
⚡ 自动生成 · <a href="https://github.com/Kalditeen/morning_brief">Kalditeen/morning_brief</a> · {datetime.now().strftime('%Y-%m-%d %H:%M')}
</div>
</body>
</html>"""


def compose_all(items, config, date_str, is_friday):
    """LLM 生成完整简报 markdown"""
    client = OpenAI(api_key=config["openai_api_key"], base_url=config["openai_base_url"])
    news_text = "\n\n".join(
        f"[{i+1}] [{it['cat']}] {it['title']}\n来源: {it['source']} | {it['hours_ago']}h前"
        for i, it in enumerate(items[:50])
    )
    prompt = f"""你是软件工程本科生的 AI & 就业晨间助手。北京时间 {date_str}。

输出精简日报（用于生成网页）。格式严格如下：

📰 晨间简报 | {date_str}

===

🤖 AI 产业
**一句话：**（今天AI领域核心事件）

· 具体事件 — 数据/细节 → 启示：可操作建议
· 具体事件 — 数据/细节 → 启示：可操作建议
（最多8条）

===

💼 就业水温
**一句话：**（就业市场核心信号）

· 具体事件 → 启示：可操作建议
（最多6条）

===

📊 {len(items)}条 · HN / V2EX / 36氪 / Google News

每条必须：· 事件 → 启示。禁止「学习XX是必备技能」废话。1800字内。"""
    resp = client.chat.completions.create(
        model=config["openai_model"],
        messages=[{"role":"system","content":prompt},{"role":"user","content":f"新闻：\n\n{news_text}"}],
        temperature=0.7, max_tokens=2400,
    )
    return resp.choices[0].message.content


def compose_friday_special(items, config, date_str):
    client = OpenAI(api_key=config["openai_api_key"], base_url=config["openai_base_url"])
    news_text = "\n\n".join(
        f"[{i+1}] [{it['cat']}] {it['title']}\n来源: {it['source']} | {it['hours_ago']}h前\n摘要: {it['summary'][:300]}"
        for i, it in enumerate(items[:60])
    )
    prompt = f"""你是软件工程本科生学术保研助手。北京时间 {date_str}。

输出周五特辑：

## 🎓 本周学术 & 保研特辑 | {date_str}

## 📄 本周论文（5-8篇）
- 英文原标题 + **一句中文摘要**（做了什么、为什么重要，面向本科生）

## 🏫 保研 & 校招动态
- 夏令营通知、推免政策、重点院校计算机方向

不编造。2800字内。"""
    resp = client.chat.completions.create(
        model=config["openai_model"],
        messages=[{"role":"system","content":prompt},{"role":"user","content":f"信息：\n\n{news_text}"}],
        temperature=0.7, max_tokens=3200,
    )
    return resp.choices[0].message.content


def extract_summary(md: str) -> str:
    """从完整 markdown 提取微信摘要（2-3行）"""
    lines = []
    for line in md.split("\n"):
        stripped = line.strip()
        if stripped.startswith("📰") or stripped.startswith("#"):
            lines.append(stripped.replace("# ", "").replace("## ", ""))
        elif stripped.startswith("**一句话：**"):
            lines.append(stripped)
        if len(lines) >= 4:
            break
    return "\n".join(lines) if lines else md[:200]


def send_wechat(md: str, webhook: str, label: str):
    """发送微信消息，自动截断超长内容"""
    max_bytes = 3800
    while len(md.encode("utf-8")) > max_bytes:
        md = md[:len(md) - 80]
    if len(md.encode("utf-8")) > 3000:
        print(f"   ⚠️ 内容 {len(md.encode('utf-8'))} 字节，已截断")
    payload = json.dumps({"msgtype":"markdown","markdown":{"content":md}}).encode()
    req = urllib.request.Request(webhook, data=payload, headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        r = json.loads(resp.read())
        if r.get("errcode") != 0:
            raise RuntimeError(f"推送失败: {r}")
    print(f"✅ {label} 已推送")


def commit_and_push(docs_dir: str):
    """提交生成的 HTML 到 git 并推送"""
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", docs_dir], check=True)
    # 有变更才 commit
    r = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    if r.returncode == 0:
        print("   📄 无变更，跳过提交")
        return
    subprocess.run(["git", "commit", "-m", f"📰 晨间简报 {datetime.now().strftime('%Y-%m-%d')}"], check=True)
    subprocess.run(["git", "push"], check=True)
    print("   ✅ HTML 页面已推送")


def main():
    print("=" * 50)
    print("📰 晨间简报 · 开始运行")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 50)

    config = load_config()
    for k in ["wechat_webhook", "openai_api_key"]:
        if not config[k]:
            raise RuntimeError(f"缺少配置: {k}")

    today = datetime.now()
    date_str = today.strftime("%Y年%m月%d日")
    wd = ["周一","周二","周三","周四","周五","周六","周日"]
    date_full = f"{date_str} {wd[today.weekday()]}"
    file_date = today.strftime("%Y-%m-%d")
    is_friday = today.weekday() == 4

    docs_dir = "docs"
    os.makedirs(docs_dir, exist_ok=True)

    # ── 日报 ──
    print("\n📡 [日报] 24h新闻…")
    daily = fetch_news(DAILY_SOURCES, 24)
    print(f"📊 {len(daily)} 条")

    print("\n🤖 LLM 生成…")
    md = compose_all(daily, config, date_full, is_friday)
    print(f"   内容: {len(md)} 字符")

    # 生成 HTML 页面
    html = md2html(md, date_full, extract_summary(md))
    html_path = os.path.join(docs_dir, f"{file_date}.html")
    with open(html_path, "w") as f:
        f.write(html)
    print(f"   📄 {html_path}")

    # 微信消息 = 摘要 + 链接
    cdn_url = f"{CDN_BASE}/{html_path}"
    summary = extract_summary(md)
    wechat_msg = f"{summary}\n\n📖 [查看完整晨报]({cdn_url})"

    print("\n📨 推送微信…")
    send_wechat(wechat_msg, config["wechat_webhook"], "日报")

    # ── 周五特辑 ──
    if is_friday:
        print("\n📡 [特辑] 一周学术+保研…")
        fri = fetch_news(FRIDAY_SOURCES, 24 * 7)
        print(f"📊 {len(fri)} 条")
        print("\n🤖 LLM 生成特辑…")
        smd = compose_friday_special(fri, config, date_full)
        print(f"   内容: {len(smd)} 字符")
        shtml = md2html(smd, date_full + " 特辑", extract_summary(smd))
        spath = os.path.join(docs_dir, f"{file_date}-special.html")
        with open(spath, "w") as f:
            f.write(shtml)
        surl = f"{CDN_BASE}/{spath}"
        swx = extract_summary(smd) + f"\n\n📖 [查看特辑]({surl})"
        send_wechat(swx, config["wechat_webhook"], "特辑")

    # ── 推送 HTML 到 repo ──
    print("\n📤 提交 HTML 页面…")
    commit_and_push(docs_dir)

    print("\n" + "=" * 50)
    print("✅ 完成" + ("（含特辑）" if is_friday else ""))
    print("=" * 50)


if __name__ == "__main__":
    main()
