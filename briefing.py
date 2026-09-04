#!/usr/bin/env python3
"""
晨间双语头条 · GitHub Actions 版
每日激活：采集 NYT + BBC 头版头条
NVIDIA AI 翻译为简体中文 + 提取四六级高频词汇
前端：英文优先，点击展开中文翻译（保留新闻图片）
通知：企业微信群机器人推送摘要 + 链接
"""

import os, re, json, subprocess, time, html as html_mod, hashlib, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BJT = timezone(timedelta(hours=8))  # 北京时间

def now_bj(): return datetime.now(BJT)

import feedparser
from openai import OpenAI

CDN_BASE = "https://kalditeen.github.io/morning_brief"

# ── 信源：NYT + BBC 头版 ──
SOURCES = [
    {"name": "纽约时报 NYT", "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "cat": "nytimes"},
    {"name": "BBC News", "url": "http://feeds.bbci.co.uk/news/rss.xml", "cat": "bbc"},
]


def load_config():
    return {
        "wechat_webhook": os.environ.get("WECHAT_WEBHOOK_URL", "").strip(),
        "openai_api_key": os.environ.get("OPENAI_API_KEY", "").strip(),
        "openai_base_url": os.environ.get("OPENAI_BASE_URL") or "https://integrate.api.nvidia.com/v1",
        "openai_model": os.environ.get("OPENAI_MODEL") or "meta/llama-3.1-8b-instruct",
    }


def extract_image(entry) -> str:
    """从 RSS entry 提取图片 URL"""
    # 优先 media_content
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            url = m.get("url", "")
            if url and "image" in m.get("type", "image"):
                return url
    # 其次 media_thumbnail
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        for m in entry.media_thumbnail:
            url = m.get("url", "")
            if url:
                return url
    # 最后从 summary/description 中提取 img 标签
    summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
    m = re.search(r'<img[^>]+src="([^"]+)"', summary)
    if m:
        return html_mod.unescape(m.group(1))
    return ""


def fetch_headlines(sources, max_per_source=8):
    """抓取头版头条，保留标题/摘要/图片/链接"""
    all_items = []
    seen = set()
    for src in sources:
        try:
            feed = feedparser.parse(src["url"])
        except Exception as e:
            print(f"  ⚠️ {src['name']}: {e}")
            continue
        count = 0
        for entry in feed.entries:
            title = html_mod.unescape(getattr(entry, "title", "").strip())
            if len(title) < 10:
                continue
            h = hashlib.md5((src["cat"] + title).encode()).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            summary = html_mod.unescape(getattr(entry, "summary", "") or getattr(entry, "description", ""))
            summary = re.sub(r"<[^>]+>", " ", summary)
            summary = re.sub(r"\s+", " ", summary).strip()[:500]
            link = getattr(entry, "link", "")
            image = extract_image(entry)
            all_items.append({
                "title": title,
                "source": src["name"],
                "cat": src["cat"],
                "summary": summary,
                "link": link,
                "image": image,
            })
            count += 1
            if count >= max_per_source:
                break
        print(f"  ✅ {src['name']}: {count} 条")
    return all_items


def translate_and_vocab(item: dict, config: dict):
    """
    用 NVIDIA AI 将新闻标题+摘要翻译成简体中文，
    并提取文章中出现的 5 个四六级高频词汇。
    返回 (中文标题, 中文摘要, 词汇列表)
    """
    client = OpenAI(api_key=config["openai_api_key"], base_url=config["openai_base_url"])
    prompt = f"""你是一名中英双语新闻编辑。请处理以下英文新闻：

标题：{item['title']}
摘要：{item['summary'][:400]}

请完成两件事，严格按以下格式输出，不要添加任何解释：

【中文标题】
（将标题翻译成简体中文）

【中文摘要】
（将摘要翻译成简体中文，保持原意，2-3句）

【高频词汇】
1. 英文单词 — 词性. 中文释义
2. 英文单词 — 词性. 中文释义
3. 英文单词 — 词性. 中文释义
4. 英文单词 — 词性. 中文释义
5. 英文单词 — 词性. 中文释义

词汇必须是这篇文章中实际出现的、四六级考试常见词汇。"""

    try:
        resp = client.chat.completions.create(
            model=config["openai_model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600,
        )
        content = resp.choices[0].message.content.strip()
        return parse_translation(content)
    except Exception as e:
        print(f"   ⚠️ 翻译失败: {e}")
        return item["title"], "", []


def parse_translation(content: str):
    """解析 AI 返回的翻译结果"""
    zh_title = ""
    zh_summary = ""
    vocab = []

    # 解析中文标题
    m = re.search(r"【中文标题】\s*\n(.*?)(?=\n\s*【中文摘要】|\Z)", content, re.DOTALL)
    if m:
        zh_title = m.group(1).strip()

    # 解析中文摘要
    m = re.search(r"【中文摘要】\s*\n(.*?)(?=\n\s*【高频词汇】|\Z)", content, re.DOTALL)
    if m:
        zh_summary = m.group(1).strip()

    # 解析词汇
    m = re.search(r"【高频词汇】\s*\n(.*?)\Z", content, re.DOTALL)
    if m:
        for line in m.group(1).strip().split("\n"):
            line = line.strip()
            if re.match(r"^\d+\.\s*", line):
                line = re.sub(r"^\d+\.\s*", "", line).strip()
                if line:
                    vocab.append(line)
    return zh_title, zh_summary, vocab[:5]


def compose_wechat_summary(items, config, date_str):
    """用 NVIDIA AI 生成微信摘要（至多3条热点）"""
    client = OpenAI(api_key=config["openai_api_key"], base_url=config["openai_base_url"])
    news_text = "\n".join(
        f"- [{it['source']}] {it['title']}"
        for it in items[:15]
    )
    prompt = f"""你是晨间简报摘要助手。{date_str}。阅读以下英文新闻标题，选出至多3条最重要/最热点的新闻，用简体中文一句话概括每条（· 开头）。突出事件本身和为什么值得关注。总字数150字内，不要标题和解释。

新闻：
{news_text}"""
    try:
        resp = client.chat.completions.create(
            model=config["openai_model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=300,
        )
        s = resp.choices[0].message.content.strip()
        return s[:400] if len(s) > 400 else s
    except Exception as e:
        print(f"   ⚠️ 微信摘要生成失败: {e}")
        return "今日双语头条已更新，点击查看完整内容。"


def build_page(items: list, date_str: str, config) -> str:
    """生成双语对照 HTML 页面（英文优先，点击展开中文）"""
    article_cards = []
    for i, it in enumerate(items):
        zh_title, zh_summary, vocab = translate_and_vocab(it, config)
        # 卡片：英文标题+图片+摘要 → 点击展开中文翻译+词汇
        title_en = html_mod.escape(it["title"])
        title_zh = html_mod.escape(zh_title) if zh_title else ""
        summary_en = html_mod.escape(it["summary"][:400]) if it["summary"] else ""
        summary_zh = html_mod.escape(zh_summary) if zh_summary else ""
        source = html_mod.escape(it["source"])
        link = html_mod.escape(it["link"]) if it["link"] else "#"
        img_html = ""
        if it["image"]:
            img_url = html_mod.escape(it["image"])
            img_html = f'<img class="article-img" src="{img_url}" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
        vocab_html = ""
        if vocab:
            vocab_items = "\n".join(
                f'<span class="vocab-item">{html_mod.escape(v)}</span>'
                for v in vocab
            )
            vocab_html = f'<div class="vocab-box"><strong>📚 四六级高频词</strong><div class="vocab-list">{vocab_items}</div></div>'

        card = f'''<article class="article">
<div class="article-en">
<h3 class="article-title-en">{title_en}</h3>
{img_html}
<p class="article-summary-en">{summary_en}</p>
</div>
<div class="article-toggle">
<button class="translate-btn" onclick="toggleZh({i})">🇨🇳 查看中文翻译</button>
</div>
<div class="article-zh" id="zh-{i}" style="display:none">
<h4 class="article-title-zh">{title_zh}</h4>
<p class="article-summary-zh">{summary_zh}</p>
{vocab_html}
</div>
<div class="article-meta"><span class="source-badge">{source}</span> <a href="{link}" target="_blank" class="source-link">阅读原文 ↗</a></div>
</article>'''
        article_cards.append(card)

    cards_html = "\n".join(article_cards)
    now_str = now_bj().strftime("%Y-%m-%d %H:%M")
    safe_date = html_mod.escape(date_str)

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta property="og:title" content="📰 晨间双语头条 | {safe_date}">
<meta property="og:description" content="纽约时报 + BBC 头版头条，双语对照，四六级词汇">
<meta property="og:type" content="article">
<title>晨间双语头条 | {safe_date}</title>
<style>
:root{{--bg:#f0f2f5;--card:#fff;--text:#1a1a2e;--muted:#888;--accent:#667eea;--accent2:#764ba2;--ai-bg:#f0f4ff;--pred-bg:#fef9e7;--sidebar-w:280px}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--text);line-height:1.75;min-height:100vh}}
.header{{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;padding:32px 36px;border-radius:16px;margin:32px auto 28px;box-shadow:0 6px 24px rgba(102,126,234,.3);max-width:860px}}
.header h1{{font-size:1.5em;font-weight:800;letter-spacing:.5px}}
.header .subtitle{{font-size:.88em;opacity:.85;margin-top:6px}}
.main{{max-width:860px;margin:0 auto;padding:0 16px 60px}}
.article{{background:var(--card);border-radius:14px;padding:22px 26px;margin-bottom:18px;box-shadow:0 2px 8px rgba(0,0,0,.04);transition:.2s}}
.article:hover{{box-shadow:0 4px 18px rgba(0,0,0,.08)}}
.article-title-en{{font-weight:700;font-size:1.12em;margin-bottom:8px;line-height:1.4;color:var(--text)}}
.article-img{{max-width:100%;height:auto;border-radius:8px;margin:8px 0;display:block}}
.article-summary-en{{font-size:.92em;color:#444;margin:6px 0}}
.article-zh{{border-top:1px solid #eee;margin-top:12px;padding-top:12px}}
.article-title-zh{{font-weight:700;font-size:1.02em;color:var(--accent2);margin-bottom:6px}}
.article-summary-zh{{font-size:.92em;color:#333;margin:6px 0}}
.translate-btn{{background:var(--accent);color:#fff;border:none;padding:6px 14px;border-radius:16px;cursor:pointer;font-size:.82em;transition:.2s}}
.translate-btn:hover{{background:var(--accent2)}}
.vocab-box{{background:var(--ai-bg);border-radius:8px;padding:10px 14px;margin-top:10px}}
.vocab-box strong{{font-size:.88em;color:var(--accent)}}
.vocab-list{{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}}
.vocab-item{{background:#fff;border:1px solid #e0e0f0;border-radius:14px;padding:3px 10px;font-size:.82em;color:#333}}
.article-meta{{display:flex;align-items:center;gap:10px;margin-top:12px;font-size:.82em}}
.source-badge{{background:#ede7f6;color:var(--accent2);border-radius:12px;padding:2px 10px;font-weight:600}}
.source-link{{color:var(--accent);text-decoration:none}}
.source-link:hover{{text-decoration:underline}}
.footer{{text-align:center;color:#999;font-size:.78em;margin-top:32px;padding-top:18px;border-top:1px solid #e0e0e0}}
.footer a{{color:var(--accent)}}
@media(max-width:480px){{
.header{{margin:16px 8px 20px;padding:22px 20px}}
.main{{padding:0 8px 40px}}
.article{{padding:16px}}
}}
</style>
</head>
<body>
<div class="header">
<h1>📰 晨间双语头条</h1>
<p class="subtitle">{safe_date} · 纽约时报 + BBC · 英文优先，点击查看中文</p>
</div>
<main class="main">
{cards_html}
<div class="footer">
⚡ 自动生成 · <a href="https://github.com/Kalditeen/morning_brief">Kalditeen/morning_brief</a> · {now_str}
</div>
</main>
<script>
function toggleZh(id){{
  var el=document.getElementById('zh-'+id);
  var btn=event.target;
  if(el.style.display==='none'){{
    el.style.display='block';
    btn.textContent='🇨🇳 收起翻译';
  }}else{{
    el.style.display='none';
    btn.textContent='🇨🇳 查看中文翻译';
  }}
}}
</script>
</body>
</html>'''


def send_wechat(md: str, webhook: str, label: str):
    """企业微信推送，带重试"""
    max_bytes = 3800
    while len(md.encode("utf-8")) > max_bytes:
        md = md[:len(md) - 80]
    payload = json.dumps({"msgtype": "markdown", "markdown": {"content": md}}).encode()
    req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    last_err = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                r = json.loads(resp.read())
                if r.get("errcode") != 0:
                    raise RuntimeError(f"推送失败:{r}")
                print(f"✅ {label} 已推送")
                return
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < 3:
                print(f"   ⚠️ 第{attempt}次超时，{attempt*5}s后重试…")
                time.sleep(attempt * 5)
    raise RuntimeError(f"推送失败(重试3次):{last_err}")


def commit_and_push(docs_dir: str):
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", docs_dir], check=True)
    r = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    if r.returncode == 0:
        print("   📄 无变更,跳过提交")
        return
    subprocess.run(["git", "commit", "-m", f"📰 晨间双语头条 {now_bj().strftime('%Y-%m-%d')}"], check=True)
    subprocess.run(["git", "push"], check=True)
    print("   ✅ HTML 页面已推送")


def wait_until_beijing(h=7, m=0):
    bj = now_bj()
    target = bj.replace(hour=h, minute=m, second=0, microsecond=0)
    sec = (target - bj).total_seconds()
    if 0 < sec < 3600:
        print(f"⏳ 等待至北京时间 {target.strftime('%H:%M')}({int(sec)}秒)…")
        time.sleep(sec)


def cleanup_old(docs_dir: str, days=7):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for f in Path(docs_dir).glob("*.html"):
        if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) < cutoff:
            f.unlink()
            print(f"   🗑 清理:{f.name}")


def main():
    print("=" * 50)
    print("📰 晨间双语头条 · 开始运行")
    print(f"⏰ {now_bj().strftime('%Y-%m-%d %H:%M:%S')} 北京时间")
    print("=" * 50)

    config = load_config()
    for k in ["wechat_webhook", "openai_api_key"]:
        if not config[k]:
            raise RuntimeError(f"缺少配置:{k}")

    today = now_bj()
    date_str = today.strftime("%Y年%m月%d日")
    wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    date_full = f"{date_str} {wd[today.weekday()]}"
    file_date = today.strftime("%Y-%m-%d")

    docs_dir = "docs"
    os.makedirs(docs_dir, exist_ok=True)

    # 1. 抓取头版头条
    print("\n📡 采集 NYT + BBC 头版头条…")
    items = fetch_headlines(SOURCES, max_per_source=8)
    print(f"📊 共采集 {len(items)} 条")

    # 2. 生成 HTML（含翻译+词汇）
    print("\n🤖 NVIDIA AI 翻译 + 提取词汇…")
    html = build_page(items, date_full, config)
    html_path = os.path.join(docs_dir, f"{file_date}.html")
    with open(html_path, "w") as f:
        f.write(html)
    print(f"   📄 {html_path}")

    # 3. 微信摘要
    print("\n🤖 生成微信摘要…")
    wx_summary = compose_wechat_summary(items, config, date_full)
    cdn_url = f'{CDN_BASE}/{html_path.replace("docs/", "")}'
    wechat_msg = f"📰 晨间双语头条 | {date_full}\n\n{wx_summary}\n\n📖 [查看完整头条]({cdn_url})"
    send_wechat(wechat_msg, config["wechat_webhook"], "日报")

    # 4. 清理旧文件 + 推送
    cleanup_old(docs_dir, 7)
    print("\n📤 提交 HTML 页面…")
    commit_and_push(docs_dir)

    wait_until_beijing(7, 0)

    print("\n" + "=" * 50)
    print("✅ 完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
