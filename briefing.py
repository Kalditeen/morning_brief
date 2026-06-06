#!/usr/bin/env python3
"""
晨间简报 · GitHub Actions 版
周一至周五：AI 产业 + 软件工程就业（精美网页 + 侧边栏 + 标签导航）
周五附加：学术论文 & 保研特辑
"""

import os, re, json, subprocess, time, html as html_mod, hashlib, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BJT = timezone(timedelta(hours=8))  # 北京时间

def now_bj(): return datetime.now(BJT)

import feedparser
from openai import OpenAI

CDN_BASE = "https://kalditeen.github.io/morning_brief"

DAILY_SOURCES = [
    {"name":"Hacker News","url":"https://hnrss.org/frontpage?count=15","cat":"ai"},
    {"name":"V2EX 热帖","url":"https://www.v2ex.com/feed/tab/hot.xml","cat":"job"},
    {"name":"36氪","url":"https://36kr.com/feed","cat":"job"},
    {"name":"Google News AI","url":"https://news.google.com/rss/search?q=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+AI+%E5%A4%A7%E6%A8%A1%E5%9E%8B+when:24h&hl=zh-CN&gl=CN&ceid=CN:zh-Hans","cat":"ai"},
    {"name":"Google News 就业","url":"https://news.google.com/rss/search?q=%E4%BA%92%E8%81%94%E7%BD%91+%E6%8B%9B%E8%81%98+%E8%A3%81%E5%91%98+%E9%9D%A2%E7%BB%8F+%E7%A8%8B%E5%BA%8F%E5%91%98+%E5%B0%B1%E4%B8%9A&hl=zh-CN&gl=CN&ceid=CN:zh-Hans","cat":"job"},
    {"name":"Google News 全球","url":"https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans","cat":"world"},
]

FRIDAY_SOURCES = [
    {"name":"ArXiv cs.AI","url":"http://export.arxiv.org/rss/cs.AI","cat":"academic"},
    {"name":"ArXiv cs.CL","url":"http://export.arxiv.org/rss/cs.CL","cat":"academic"},
    {"name":"Reddit ML","url":"https://www.reddit.com/r/MachineLearning/.rss","cat":"academic"},
    {"name":"保研 知乎","url":"https://news.google.com/rss/search?q=%E4%BF%9D%E7%A0%94+%E5%A4%8F%E4%BB%A4%E8%90%A5+%E6%8E%A8%E5%85%8D+site:zhihu.com&hl=zh-CN&gl=CN&ceid=CN:zh-Hans","cat":"baoyan"},
    {"name":"保研 综合","url":"https://news.google.com/rss/search?q=%E4%BF%9D%E7%A0%94+%E5%A4%8F%E4%BB%A4%E8%90%A5+%E6%8E%A8%E5%85%8D+%E7%A0%94%E7%A9%B6%E7%94%9F%E6%8B%9B%E7%94%9F&hl=zh-CN&gl=CN&ceid=CN:zh-Hans","cat":"baoyan"},
    {"name":"校招 实习","url":"https://news.google.com/rss/search?q=%E8%BD%AF%E4%BB%B6%E5%B7%A5%E7%A8%8B+%E8%AE%A1%E7%AE%97%E6%9C%BA+%E6%A0%A1%E6%8B%9B+%E5%AE%9E%E4%B9%A0&hl=zh-CN&gl=CN&ceid=CN:zh-Hans","cat":"baoyan"},
    {"name":"南理工研招","url":"https://news.google.com/rss/search?q=site:njust.edu.cn+%E7%A0%94%E7%A9%B6%E7%94%9F+%E6%8E%A8%E5%85%8D+%E5%A4%8F%E4%BB%A4%E8%90%A5&hl=zh-CN&gl=CN&ceid=CN:zh-Hans","cat":"baoyan"},
    {"name":"南航研招","url":"https://news.google.com/rss/search?q=site:nuaa.edu.cn+%E7%A0%94%E7%A9%B6%E7%94%9F+%E6%8E%A8%E5%85%8D+%E5%A4%8F%E4%BB%A4%E8%90%A5&hl=zh-CN&gl=CN&ceid=CN:zh-Hans","cat":"baoyan"},
    {"name":"华东211研招","url":"https://news.google.com/rss/search?q=%E5%A4%8F%E4%BB%A4%E8%90%A5+%E6%8E%A8%E5%85%8D+%E8%AE%A1%E7%AE%97%E6%9C%BA&hl=zh-CN&gl=CN&ceid=CN:zh-Hans","cat":"baoyan"},
]


def load_config():
    return {
        "wechat_webhook": os.environ.get("WECHAT_WEBHOOK_URL",""),
        "openai_api_key": os.environ.get("OPENAI_API_KEY",""),
        "openai_base_url": os.environ.get("OPENAI_BASE_URL","https://api.openai.com/v1"),
        "openai_model": os.environ.get("OPENAI_MODEL","deepseek-v4-pro"),
    }


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
            if hasattr(e,"published_parsed") and e.published_parsed:
                pt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(e,"updated_parsed") and e.updated_parsed:
                pt = datetime(*e.updated_parsed[:6], tzinfo=timezone.utc)
            if pt and pt < cutoff: continue
            t = html_mod.unescape(getattr(e,"title","").strip())
            if len(t) < 6: continue
            hh = hashlib.md5((src["cat"]+t).encode()).hexdigest()
            if hh in seen: continue
            seen.add(hh)
            s = html_mod.unescape(getattr(e,"summary","") or getattr(e,"description",""))
            s = re.sub(r"<[^>]+>","",s)[:400]
            link = getattr(e,"link","")
            ha = round((now-pt).total_seconds()/3600,1) if pt else None
            all_items.append({"title":t,"source":src["name"],"cat":src["cat"],"hours_ago":ha,"summary":s.strip(),"link":link})
            cnt += 1
        print(f"  ✅ {src['name']}: {cnt} 条")
    all_items.sort(key=lambda x:-(x["hours_ago"] or 0),reverse=True)
    return all_items


def compose_all(items, config, date_str):
    client = OpenAI(api_key=config["openai_api_key"],base_url=config["openai_base_url"])
    news_text = "\n\n".join(
        f"[{i+1}] [{it['cat']}] {it['title']}\n来源:{it['source']} | {it['hours_ago']}h前"
        for i,it in enumerate(items[:50])
    )
    prompt = f"""你是软件工程本科生的AI & 就业晨间助手。北京时间 {date_str}。
    规则: ①趋势必须基于所给的新闻概括，禁止空洞描述 ②每条必须含新闻内容 ③所有英文翻译为中文再输出
分析以下新闻，输出日报。严格按此格式:

📰 晨间简报 | {date_str}

===

🤖 AI 产业
**趋势:** （用2-3句话概括今天AI领域整体动向，必须基于真实新闻）

（从以下新闻中提取最多10条，每条格式如下）
· [公司/产品名称] — [新闻/事件具体内容]
  → 启示: [学生可以跟进的关注方向，一句话]

===

💼 就业水温
**趋势:** （用2-3句话概括今天就业市场信号，必须基于真实新闻）

（最多10条）
· [公司/事件] — [新闻/事件具体内容]
  → 启示: [学生可以跟进的学习方向和可操作建议，一句话]

===

🌍 时事
**趋势:** （用2-3句话概括过去24小时全球最重要变化）

（最多10条）
· [事件] — [新闻/事件具体内容]
  → 影响: [可能对普通人的影响，一句话]

===
📊 {len(items)}条

"""
    resp = client.chat.completions.create(
        model=config["openai_model"],
        messages=[{"role":"system","content":prompt},{"role":"user","content":f"新闻:\n\n{news_text}"}],
        temperature=0.7,max_tokens=3200,
    )
    return resp.choices[0].message.content


def compose_friday_special(items, config, date_str):
    client = OpenAI(api_key=config["openai_api_key"],base_url=config["openai_base_url"])
    # Academic items: ArXiv + Reddit; Baoyan items: Google News baoyan sources
    academic = [it for it in items if it["cat"]=="academic"]
    baoyan = [it for it in items if it["cat"]=="baoyan"]
    paper_text = "\n".join(f"{i+1}. {it['title']}" for i,it in enumerate(academic[:30]))
    baoyan_text = "\n".join(f"{i+1}. {it['title']} (来源:{it['source']})" for i,it in enumerate(baoyan[:20]))
    prompt = f"""你是软件工程本科生的学术保研助手。北京时间{date_str}。请分析以下信息并输出周五特辑。

第一行必须：🎓 学术&保研特辑 | {date_str}

然后严格按此结构输出:

📄 本周论文
从30篇论文中精选5-8篇最重要的。每条必须包含:
- 原文标题
- 原文摘要（翻译成中文输出）
- **中文一句话摘要**（这篇论文做了什么，为什么值得本科生关注）

🏫 保研&校招动态
整理保研、夏令营、推免、校招相关信息。每条一句话说明。

最后一行: 📊 论文X篇 + 保研/就业Y条

禁止输出格式说明。只输出内容。2000字。"""
    resp = client.chat.completions.create(
        model=config["openai_model"],
        messages=[{"role":"system","content":prompt},{"role":"user","content":f"本周论文:\n{paper_text}\n\n保研信息:\n{baoyan_text}"}],
        temperature=0.7,max_tokens=2500,
    )
    return resp.choices[0].message.content


# ── HTML 页面生成（侧边栏 + 标签导航 + 美化）──

def build_page(md: str, items: list, date_str: str, title: str, is_special: bool = False) -> str:
    # 按 cat 分组
    ai_items = [it for it in items if it["cat"] in ("ai","academic")]
    job_items = [it for it in items if it["cat"] in ("job","baoyan")]
    all_for_sidebar = items[:80]

    def render_sidebar():
        rows = []
        for it in all_for_sidebar:
            cat_icon = {"ai":"🤖","job":"💼","world":"🌍","academic":"📄","baoyan":"🏫"}.get(it["cat"],"📌")
            link = html_mod.escape(it.get("link","#"))
            t = html_mod.escape(it["title"][:60])
            src = html_mod.escape(it["source"])
            ha = it.get("hours_ago","?")
            rows.append(f'<a class="src-item" href="{link}" target="_blank" title="{t}"><span class="src-icon">{cat_icon}</span><span class="src-text">{t}</span><span class="src-meta">{src} · {ha}h</span></a>')
        return "\n".join(rows)

    # markdown → HTML section cards
    def md2cards(md: str) -> str:
        lines = md.split("\n")
        out = []
        card_open = False
        buf = []
        current_section = ""

        def flush_card():
            nonlocal card_open, buf, current_section
            if not buf: return
            html = "\n".join(buf)
            icon_map = {"🤖":"🤖","💼":"💼","📄":"📄","🏫":"🏫","🎓":"🎓","📰":"📰"}
            sec_id = current_section.strip().split()[0] if current_section else ""
            # Find matching icon by partial key match
            sec_icon = "📌"
            for key in icon_map:
                if key in sec_id:
                    sec_icon = icon_map[key]
                    break
            out.append(f'<div class="card" id="sec-{html_mod.escape(sec_id)}"><div class="card-head">{sec_icon} {html_mod.escape(current_section)}</div><div class="card-body">{html}</div></div>')
            buf = []
            card_open = False

        for line in lines:
            stripped = line.strip()
            # section header: === or ##
            if stripped == "===" or stripped.startswith("## "):
                flush_card()
                continue
            # detect next section
            if stripped and not stripped.startswith("·") and not stripped.startswith(">") and not stripped.startswith("**") and not stripped.startswith("-") and not stripped.startswith("📊") and not stripped.startswith("📰"):
                if any(kw in stripped for kw in ["🤖","💼","🌍","📄","🏫","🎓"]):
                    flush_card()
                    current_section = stripped
                    card_open = True
                    continue
            if not card_open and stripped:
                current_section = stripped if "🤖" in stripped or "💼" in stripped else current_section
                card_open = True

            # render line
            if stripped.startswith("**趋势"):
                buf.append(f'<div class="trend-box">{_md_inline(stripped)}</div>')
            elif stripped.startswith("  → 启示") or stripped.startswith("→ 启示"):
                buf.append(f'<div class="news-insight">{_md_inline(stripped.strip())}</div>')
            elif stripped.startswith("· "):
                text = stripped[2:]
                # split into event / summary / prediction
                parts = re.split(r"[📊🔮]", text)
                event = parts[0].strip() if parts else text
                ai_part = ""
                pred_part = ""
                for p in parts[1:]:
                    p = p.strip()
                    if "总结" in text[text.index("📊"):text.index("📊")+10] if "📊" in text else False:
                        ai_part = p
                    if "预测" in text:
                        pred_part = p
                # Parse: 一句话 / · event / → insight
                event = text.strip()
                # If starts with 一句话:, render as summary
                if event.startswith("一句话:") or event.startswith("一句话趋势") or event.startswith("一句话水温"):
                    event = event  # keep as-is, will be rendered
                buf.append(f'<div class="news-item"><div class="news-title">{_md_inline(event)}</div>')
                buf.append('</div>')
            elif stripped.startswith("📰"):
                buf.append(f'<h1 class="page-title">{_md_inline(stripped)}</h1>')
            elif stripped.startswith("📊"):
                buf.append(f'<div class="stats-bar">{_md_inline(stripped)}</div>')
            elif stripped.startswith("> "):
                buf.append(f'<blockquote>{_md_inline(stripped[2:])}</blockquote>')
            elif stripped:
                buf.append(f'<p>{_md_inline(stripped)}</p>')

        flush_card()
        return "\n".join(out)

    def _md_inline(text: str) -> str:
        text = html_mod.escape(text)
        text = re.sub(r"\*\*(.+?)\*\*",r"<strong>\1</strong>",text)
        return text

    cards_html = md2cards(md)
    sidebar_html = render_sidebar()
    now_str = now_bj().strftime("%Y-%m-%d %H:%M")
    special_class = "special" if is_special else ""

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta property="og:title" content="📰 晨间简报 | {html_mod.escape(date_str)}">
<meta property="og:description" content="{html_mod.escape(title)}">
<meta property="og:type" content="article">
<title>晨间简报 | {html_mod.escape(date_str)}</title>
<style>
:root{{--bg:#f0f2f5;--card:#fff;--text:#1a1a2e;--muted:#888;--accent:#667eea;--accent2:#764ba2;--ai-bg:#f0f4ff;--pred-bg:#fef9e7;--sidebar-w:280px}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--text);line-height:1.75;display:flex;min-height:100vh}}

/* 侧边栏 */
.sidebar{{width:var(--sidebar-w);background:#1e1e2e;color:#ccc;position:fixed;top:0;left:0;bottom:0;overflow-y:auto;z-index:100;padding:16px 0;font-size:0.82em;transition:transform .25s}}
.sidebar h2{{color:#fff;font-size:1em;padding:0 16px 10px;border-bottom:1px solid #333;margin-bottom:8px}}
.sidebar-toggle{{display:none;position:fixed;top:12px;left:12px;z-index:200;background:var(--accent);color:#fff;border:none;width:36px;height:36px;border-radius:8px;font-size:1.2em;cursor:pointer}}
.src-item{{display:flex;align-items:flex-start;gap:8px;padding:6px 16px;text-decoration:none;color:#b0b0c0;border-left:2px solid transparent;transition:.15s}}
.src-item:hover,.src-item:focus{{background:#2a2a3e;color:#fff;border-left-color:var(--accent)}}
.src-icon{{flex-shrink:0;font-size:1em}}
.src-text{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.src-meta{{flex-shrink:0;font-size:0.75em;color:#666}}

/* 主内容 */
.main{{margin-left:var(--sidebar-w);flex:1;padding:32px 40px 60px;max-width:860px}}

/* 顶部 header */
.header{{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;padding:32px 36px;border-radius:16px;margin-bottom:28px;box-shadow:0 6px 24px rgba(102,126,234,.3)}}
.header h1{{font-size:1.5em;font-weight:800;letter-spacing:.5px}}
.header .subtitle{{font-size:.88em;opacity:.85;margin-top:6px}}

/* 标签导航 */
.tabs{{display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap}}
.tab{{padding:8px 18px;border-radius:20px;border:none;background:#fff;color:var(--text);font-size:.88em;cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,.06);transition:.2s;text-decoration:none}}
.tab:hover,.tab.active{{background:var(--accent);color:#fff}}

/* 卡片 */
.card{{background:var(--card);border-radius:14px;padding:22px 26px;margin-bottom:18px;box-shadow:0 2px 8px rgba(0,0,0,.04);transition:.2s}}
.card:hover{{box-shadow:0 4px 18px rgba(0,0,0,.08)}}
.card-head{{font-size:1.1em;font-weight:700;margin-bottom:14px;padding-bottom:10px;border-bottom:2px solid #667eea18;color:var(--accent)}}
.card-body p{{margin:6px 0;font-size:.94em}}

/* 一句话 */
.one-liner{{background:linear-gradient(135deg,#f0f4ff,#ede7f6);border-radius:8px;padding:10px 16px;margin:10px 0;font-weight:600;font-size:.95em;border-left:3px solid var(--accent)}}
.trend-box{{background:linear-gradient(135deg,#f3e5f5,#ede7f6);border-radius:10px;padding:14px 18px;margin:12px 0;font-size:.94em;line-height:1.7;border-left:4px solid var(--accent2)}}

/* 新闻条目 */
.news-item{{border-bottom:1px solid #eee;padding:14px 0}}
.news-item:last-child{{border-bottom:none}}
.news-title{{font-weight:700;font-size:1.02em;margin-bottom:6px;line-height:1.5}}
.news-ai{{background:var(--ai-bg);border-radius:6px;padding:6px 12px;margin:4px 0;font-size:.88em}}
.news-pred{{background:var(--pred-bg);border-radius:6px;padding:6px 12px;margin:4px 0;font-size:.88em;border-left:3px solid #f0b90b}}
.news-summary{{font-weight:600;font-size:1em;margin:8px 0 4px;color:var(--accent)}}
.news-insight{{padding-left:14px;margin:3px 0;font-size:.9em;color:#555;border-left:3px solid #90caf9}}
.label{{font-weight:700;font-size:.85em;margin-right:4px}}

.page-title{{font-size:1.3em;margin:10px 0}}
.stats-bar{{color:var(--muted);font-size:.85em;text-align:center;padding:10px}}
blockquote{{border-left:3px solid var(--accent);padding:8px 16px;margin:12px 0;background:#f8f9ff;border-radius:0 8px 8px 0;font-size:.92em}}

.footer{{text-align:center;color:#999;font-size:.78em;margin-top:32px;padding-top:18px;border-top:1px solid #e0e0e0}}
.footer a{{color:var(--accent)}}

/* 响应式 */
@media(max-width:768px){{
.sidebar{{transform:translateX(-100%)}}
.sidebar.open{{transform:translateX(0)}}
.sidebar-toggle{{display:block}}
.main{{margin-left:0;padding:20px 16px 40px}}
.header{{padding:22px 20px}}
.card{{padding:16px}}
}}
</style>
</head>
<body class="{special_class}">
<button class="sidebar-toggle" onclick="document.querySelector('.sidebar').classList.toggle('open')">☰</button>

<aside class="sidebar">
<h2>📡 信息源 ({len(all_for_sidebar)}条)</h2>
{sidebar_html}
</aside>

<main class="main">
<div class="header">
<h1>📰 晨间简报</h1>
<p class="subtitle">{html_mod.escape(date_str)}</p>
</div>

<div class="tabs">
<a class="tab active" href="#sec-🤖">🤖 AI 产业</a>
<a class="tab" href="#sec-💼">💼 就业水温</a>
<a class="tab" href="#sec-🌍">🌍 时事</a>
<a class="tab" href="#" onclick="document.querySelector('.sidebar').classList.toggle('open');return false">📡 全部信源</a>
</div>

{cards_html}

<div class="footer">
⚡ 自动生成 · <a href="https://github.com/Kalditeen/morning_brief">Kalditeen/morning_brief</a> · {now_str}
</div>
</main>

<script>
// tab 切换高亮
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',function(){{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  if(!this.getAttribute('onclick')) this.classList.add('active');
}}));
</script>
</body>
</html>'''


def extract_summary(md: str) -> str:
    lines = []
    for line in md.split("\n"):
        s = line.strip()
        if s.startswith("📰") or s.startswith("#"):
            lines.append(s.replace("# ","").replace("## ",""))
        elif s.startswith("**趋势") or s.startswith("**一句话"):
            lines.append(s[:120])
        if len(lines) >= 5: break
    return "\n".join(lines) if lines else md[:200]


def send_wechat(md: str, webhook: str, label: str):
    max_bytes = 3800
    while len(md.encode("utf-8")) > max_bytes:
        md = md[:len(md)-80]
    payload = json.dumps({"msgtype":"markdown","markdown":{"content":md}}).encode()
    req = urllib.request.Request(webhook,data=payload,headers={"Content-Type":"application/json"},method="POST")
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=30) as resp:
                r = json.loads(resp.read())
                if r.get("errcode")!=0:
                    raise RuntimeError(f"推送失败:{r}")
                print(f"✅ {label} 已推送")
                return
        except Exception as e:
            last_err = e
            if attempt < 2:
                print(f"   ⚠️ 第{attempt+1}次失败，{3-attempt-1}秒后重试…")
                time.sleep(3)
    raise RuntimeError(f"推送失败(重试3次): {last_err}")


def commit_and_push(docs_dir: str):
    subprocess.run(["git","config","user.name","github-actions[bot]"],check=True)
    subprocess.run(["git","config","user.email","github-actions[bot]@users.noreply.github.com"],check=True)
    subprocess.run(["git","add",docs_dir],check=True)
    r = subprocess.run(["git","diff","--cached","--quiet"],capture_output=True)
    if r.returncode == 0:
        print("   📄 无变更,跳过提交")
        return
    subprocess.run(["git","commit","-m",f"📰 晨间简报 {now_bj().strftime('%Y-%m-%d')}"],check=True)
    subprocess.run(["git","push"],check=True)
    print("   ✅ HTML 页面已推送")


def wait_until_beijing(h=7,m=0):
    bj = datetime.now(timezone(timedelta(hours=8)))
    target = bj.replace(hour=h,minute=m,second=0,microsecond=0)
    sec = (target-bj).total_seconds()
    if 0 < sec < 3600:
        print(f"⏳ 等待至北京时间 {target.strftime('%H:%M')}({int(sec)}秒)…")
        time.sleep(sec)


def cleanup_old(docs_dir: str, days=7):
    cutoff = now_bj()-timedelta(days=days)
    for f in Path(docs_dir).glob("*.html"):
        if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink()
            print(f"   🗑 清理:{f.name}")


def main():
    print("="*50)
    print("📰 晨间简报 · 开始运行")
    print(f"⏰ {now_bj().strftime('%Y-%m-%d %H:%M:%S')} 北京时间")
    print("="*50)

    config = load_config()
    for k in ["wechat_webhook","openai_api_key"]:
        if not config[k]: raise RuntimeError(f"缺少配置:{k}")

    today = now_bj()
    date_str = today.strftime("%Y年%m月%d日")
    wd = ["周一","周二","周三","周四","周五","周六","周日"]
    date_full = f"{date_str} {wd[today.weekday()]}"
    file_date = today.strftime("%Y-%m-%d")
    is_friday = today.weekday()==4

    docs_dir = "docs"
    os.makedirs(docs_dir,exist_ok=True)

    # ── 日报 ──
    print("\n📡 [日报] 24h新闻…")
    daily = fetch_news(DAILY_SOURCES,24)
    print(f"📊 {len(daily)} 条")
    print("\n🤖 LLM 生成…")
    md = compose_all(daily,config,date_full)
    print(f"   内容:{len(md)} 字符")

    html = build_page(md,daily,date_full,extract_summary(md))
    html_path = os.path.join(docs_dir,f"{file_date}.html")
    with open(html_path,"w") as f:
        f.write(html)
    print(f"   📄 {html_path}")

    cdn_url = f'{CDN_BASE}/{html_path.replace("docs/","")}'
    summary = extract_summary(md)
    wechat_msg = f"{summary}\n\n📖 [查看完整晨报]({cdn_url})"
    send_wechat(wechat_msg,config["wechat_webhook"],"日报")

    # ── 周五特辑 ──
    if is_friday:
        print("\n📡 [特辑] 一周学术+保研…")
        fri = fetch_news(FRIDAY_SOURCES,24*7)
        print(f"📊 {len(fri)} 条")
        print("\n🤖 LLM 生成特辑…")
        smd = compose_friday_special(fri,config,date_full)
        print(f"   内容:{len(smd)} 字符")
        shtml = build_page(smd,fri,date_full+" 特辑",extract_summary(smd),is_special=True)
        spath = os.path.join(docs_dir,f"{file_date}-special.html")
        with open(spath,"w") as f:
            f.write(shtml)
        surl = f'{CDN_BASE}/{spath.replace("docs/","")}'
        swx = extract_summary(smd)+f"\n\n📖 [查看特辑]({surl})"
        send_wechat(swx,config["wechat_webhook"],"特辑")
        print("\n🗑 清理一周前旧文件…")
        cleanup_old(docs_dir,7)

    print("\n📤 提交 HTML 页面…")
    commit_and_push(docs_dir)
    wait_until_beijing(7,0)

    print("\n"+"="*50)
    print("✅ 完成"+("（含特辑）" if is_friday else ""))
    print("="*50)


if __name__=="__main__":
    main()
