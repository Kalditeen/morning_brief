#!/usr/bin/env python3
"""
晨间双语头条 · GitHub Actions 版
每日激活：轮换领域采集 Guardian + BBC 各 1 篇头条
爬取完整正文，AI 翻译为简体中文 + 提取四六级高频词汇
前端：每日总览页 + 原文子页；桌面端左右分栏，移动端标签切换
通知：企业微信群机器人只推送新闻标题 + 子页链接
过去 3 天标题去重，重复时自动换下一篇文章
"""

import os, re, json, subprocess, time, html as html_mod, hashlib, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BJT = timezone(timedelta(hours=8))  # 北京时间

def now_bj(): return datetime.now(BJT)

import feedparser
from openai import OpenAI
from bs4 import BeautifulSoup

CDN_BASE = "https://kalditeen.github.io/morning_brief"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# ── 信源：Guardian + BBC ──
SOURCES = [
    {
        "name": "卫报 The Guardian",
        "url": "https://www.theguardian.com/world/rss",
        "cat": "guardian",
    },
    {
        "name": "卫报 The Guardian (科技)",
        "url": "https://www.theguardian.com/technology/rss",
        "cat": "guardian-tech",
    },
    {
        "name": "卫报 The Guardian (环境)",
        "url": "https://www.theguardian.com/environment/rss",
        "cat": "guardian-env",
    },
    {
        "name": "卫报 The Guardian (科学)",
        "url": "https://www.theguardian.com/science/rss",
        "cat": "guardian-science",
    },
    {
        "name": "卫报 The Guardian (商业)",
        "url": "https://www.theguardian.com/business/rss",
        "cat": "guardian-business",
    },
    {
        "name": "BBC News (国际)",
        "url": "http://feeds.bbci.co.uk/news/world/rss.xml",
        "cat": "bbc-world",
    },
    {
        "name": "BBC News (科技)",
        "url": "http://feeds.bbci.co.uk/news/technology/rss.xml",
        "cat": "bbc-tech",
    },
    # BBC 官方将环境与科学合并在同一个 RSS，因此这里拆成两个轮换槽位，但订阅源相同
    {
        "name": "BBC News (环境)",
        "url": "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "cat": "bbc-env",
    },
    {
        "name": "BBC News (科学)",
        "url": "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "cat": "bbc-science",
    },
    {
        "name": "BBC News (商业)",
        "url": "http://feeds.bbci.co.uk/news/business/rss.xml",
        "cat": "bbc-business",
    },
]

# ── 每日领域轮换：国际、科技、环境、科学、商业 ──
# 前 5 个槽位给卫报，后 5 个槽位给 BBC；BBC 段整体向后错一位，保证 5+x 永不同领域。
DOMAIN_CYCLE = [
    {"cat": "guardian", "label": "国际"},
    {"cat": "guardian-tech", "label": "科技"},
    {"cat": "guardian-env", "label": "环境"},
    {"cat": "guardian-science", "label": "科学"},
    {"cat": "guardian-business", "label": "商业"},
    {"cat": "bbc-tech", "label": "科技"},
    {"cat": "bbc-env", "label": "环境"},
    {"cat": "bbc-science", "label": "科学"},
    {"cat": "bbc-business", "label": "商业"},
    {"cat": "bbc-world", "label": "国际"},
]

GUARDIAN_SLOTS = DOMAIN_CYCLE[:5]
BBC_SLOTS = DOMAIN_CYCLE[5:]
DOMAIN_LABEL_BY_CAT = {slot["cat"]: slot["label"] for slot in DOMAIN_CYCLE}


def source_domain_label(src: dict) -> str:
    """返回信源当前轮换槽位的领域名称"""
    return DOMAIN_LABEL_BY_CAT.get(src.get("cat", ""), src.get("cat", "未知"))


def rotate_slots(slots: list, start: int) -> list:
    """从指定位置开始，返回循环后的槽位顺序"""
    if not slots:
        return []
    start %= len(slots)
    return slots[start:] + slots[:start]


def daily_source_orders(sources: list, day=None):
    """按星期为卫报选 x 槽位，为 BBC 选 5+x 槽位"""
    day = day or now_bj()
    by_cat = {s["cat"]: s for s in sources}
    x = day.weekday() % 5
    guardian_order = [
        by_cat[slot["cat"]]
        for slot in rotate_slots(GUARDIAN_SLOTS, x)
        if slot["cat"] in by_cat
    ]
    bbc_order = [
        by_cat[slot["cat"]]
        for slot in rotate_slots(BBC_SLOTS, x)
        if slot["cat"] in by_cat
    ]
    return guardian_order, bbc_order


# ── 四六级常考话题关键词（标题粗筛）──
RELEVANT_KEYWORDS = [
    "technology", "tech", "ai", "artificial intelligence", "robot", "digital",
    "smartphone", "internet", "cyber", "data", "algorithm", "software", "app",
    "computer", "online", "social media", "privacy", "hack", "chip", "semiconductor",
    "climate", "environment", "green", "carbon", "emission", "pollution",
    "renewable", "energy", "sustainable", "biodiversity", "wildlife", "ocean",
    "plastic", "recycling", "global warming", "extreme weather", "flood", "drought",
    "health", "medical", "medicine", "disease", "cancer", "virus", "vaccine",
    "mental health", "obesity", "diet", "exercise", "sleep", "brain", "gene",
    "drug", "therapy", "hospital", "pandemic", "nutrition",
    "education", "school", "university", "student", "teacher", "learning",
    "social", "society", "culture", "community", "equality", "diversity",
    "immigration", "refugee", "poverty", "housing", "urban", "family",
    "economy", "economic", "business", "market", "finance", "financial",
    "trade", "inflation", "job", "employment", "wage", "tax", "bank",
    "investment", "startup", "industry", "manufacturing", "globalization",
    "science", "research", "study", "discovery", "space", "nasa", "physics",
    "chemistry", "biology", "evolution", "experiment", "scientist",
    "international", "global", "world", "war", "peace", "diplomacy",
    "united nations", "european union", "nato", "democracy", "election",
    "government", "policy", "law", "rights", "protest", "conflict",
]


def _is_relevant(title: str) -> bool:
    """标题关键词粗筛：命中任一关键词则返回 True"""
    low = title.lower()
    return any(kw in low for kw in RELEVANT_KEYWORDS)


def load_config():
    return {
        "wechat_webhook": os.environ.get("WECHAT_WEBHOOK_URL", "").strip(),
        "openai_api_key": os.environ.get("OPENAI_API_KEY", "").strip(),
        "openai_base_url": os.environ.get("OPENAI_BASE_URL") or "https://integrate.api.nvidia.com/v1",
        "openai_model": os.environ.get("OPENAI_MODEL") or "meta/llama-3.3-70b-instruct",
        "openai_fallback_models": [m.strip() for m in os.environ.get("OPENAI_FALLBACK_MODELS", "").split(",") if m.strip()] or [
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "deepseek-ai/deepseek-r1",
            "qwen/qwen3-235b-a22b-instruct-2507",
        ],
    }


def extract_image(entry) -> str:
    """从 RSS entry 提取图片 URL"""
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            url = m.get("url", "")
            if url and "image" in m.get("type", "image"):
                return url
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        for m in entry.media_thumbnail:
            url = m.get("url", "")
            if url:
                return url
    summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
    m = re.search(r'<img[^>]+src="([^"]+)"', summary)
    if m:
        return html_mod.unescape(m.group(1))
    return ""


def title_key(title: str) -> str:
    """标题归一化，用于最近 3 天去重"""
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def scrape_article_text(url: str, cat: str, max_paragraphs: int = 50) -> str:
    """抓取并抽取新闻正文，失败时返回空字符串"""
    if not url:
        return ""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        html = raw.decode(charset, errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        selectors = [
            "div[data-gu-name='body'] p",
            "div[data-component='text-block'] p",
            "section[name='articleBody'] p",
            "article p",
            "main p",
        ]
        paragraphs = []
        seen = set()
        for selector in selectors:
            for p in soup.select(selector):
                text = p.get_text(" ", strip=True)
                if len(text) < 20 or text in seen:
                    continue
                seen.add(text)
                paragraphs.append(text)
                if len(paragraphs) >= max_paragraphs:
                    break
            if len(paragraphs) >= max_paragraphs:
                break
        return "\n".join(paragraphs)
    except Exception as e:
        print(f"   ⚠️ 抓取正文失败 {cat}: {e}")
        return ""


def load_recent_titles(docs_dir: str, days=3):
    """读取过去 days 天的标题，返回去重 key 集合和历史列表"""
    history_path = Path(docs_dir) / "history.json"
    cutoff_date = (now_bj() - timedelta(days=days)).strftime("%Y-%m-%d")
    recent = set()
    history = []
    if history_path.exists():
        try:
            data = json.loads(history_path.read_text())
            for row in data.get("articles", []):
                date = row.get("date", "")
                title = row.get("title", "")
                if date >= cutoff_date and title:
                    history.append(row)
                    recent.add(title_key(title))
        except Exception as e:
            print(f"   ⚠️ 读取历史标题失败: {e}")
    return recent, history


def save_history(docs_dir: str, history: list, items: list, file_date: str, days=3):
    """保存今天选中的标题，并只保留过去 days 天"""
    for it in items:
        history.append({"date": file_date, "cat": it["cat"], "title": it["title"]})
    cutoff_date = (now_bj() - timedelta(days=days)).strftime("%Y-%m-%d")
    history = [row for row in history if row.get("date", "") >= cutoff_date]
    dedup = {}
    for row in history:
        dedup[(row["date"], row["cat"], title_key(row["title"]))] = row
    Path(docs_dir).mkdir(parents=True, exist_ok=True)
    (Path(docs_dir) / "history.json").write_text(
        json.dumps({"articles": list(dedup.values())}, ensure_ascii=False, indent=2)
    )


def fetch_headlines(sources, recent_titles=None, max_per_source=1, request_delay=1.5):
    """抓取头版头条，跳过无关话题、最近 3 天重复标题，并抓取全文"""
    recent_titles = recent_titles or set()
    all_items = []
    seen = set()
    first_request = True
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
            if not _is_relevant(title):
                continue
            key = title_key(title)
            if key in seen:
                continue
            if key in recent_titles:
                print(f"   ⏭️ 跳过重复标题 {src['name']}: {title[:60]}")
                continue
            seen.add(key)
            summary = html_mod.unescape(getattr(entry, "summary", "") or getattr(entry, "description", ""))
            summary = re.sub(r"<[^>]+>", " ", summary)
            summary = re.sub(r"\s+", " ", summary).strip()
            link = getattr(entry, "link", "")
            image = extract_image(entry)

            content = ""
            if link:
                if not first_request:
                    time.sleep(request_delay)
                first_request = False
                content = scrape_article_text(link, src["cat"])

            all_items.append({
                "title": title,
                "source": src["name"],
                "cat": src["cat"],
                "summary": summary,
                "content": content,
                "link": link,
                "image": image,
            })
            count += 1
            if count >= max_per_source:
                break
        print(f"  ✅ {src['name']}: {count} 篇")
    return all_items


def fetch_first_from_group(sources: list, recent_titles: set, exclude_domains=None) -> dict:
    """按给定轮换顺序，从信源组中抓取第一篇可用新闻"""
    exclude_domains = exclude_domains or set()
    for src in sources:
        if exclude_domains and source_domain_label(src) in exclude_domains:
            continue
        items = fetch_headlines([src], recent_titles=recent_titles, max_per_source=1)
        if items:
            return items[0]
    return None


def split_text_by_length(text: str, max_chars: int = 1500) -> list:
    """把长文本按长度拆成多块，优先在换行或句号处切分"""
    if not text:
        return []
    chunks = []
    remaining = text
    while len(remaining) > max_chars:
        cut = remaining.rfind("\n", 0, max_chars)
        if cut == -1:
            cut = remaining.rfind(". ", 0, max_chars)
            if cut == -1:
                cut = max_chars
            else:
                cut += 2
        else:
            cut += 1
        chunk = remaining[:cut].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _ai_call(client, config, prompt: str, max_tokens: int = 2000) -> str:
    """调用 AI，主模型 410 下线时自动切换备用模型"""
    models = [config["openai_model"]] + config.get("openai_fallback_models", [])
    tried = set()
    last_error = None
    for model in models:
        if model in tried:
            continue
        tried.add(model)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=max_tokens,
            )
            result = (resp.choices[0].message.content or "").strip()
            if model != config["openai_model"]:
                print(f"   🔁 已切换可用模型: {model}")
                config["openai_model"] = model
            return result
        except Exception as e:
            last_error = e
            status = getattr(e, "status_code", None)
            if status != 410 and "410" not in str(e):
                print(f"   ⚠️ AI 调用失败({model}): {e}")
                break
            print(f"   ⚠️ 模型已下线，跳过: {model}")
    print(f"   ⚠️ AI 调用失败: {last_error}")
    return ""


def _parse_vocab(raw: str) -> list:
    """把 AI 返回的词汇文本解析成列表，最多 5 条"""
    vocab = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^\d+\s*[\.、\)]\s*", "", line)
        if line:
            vocab.append(line)
        if len(vocab) >= 5:
            break
    return vocab


def translate_and_vocab(item: dict, config: dict, max_chars: int = 1500):
    """AI 翻译标题+正文（分段翻译），并提取 5 个四六级高频词汇"""
    client = OpenAI(api_key=config["openai_api_key"], base_url=config["openai_base_url"])

    title = (item.get("title") or "").strip()
    body = (item.get("content") or item.get("summary") or "").strip()

    if not title and not body:
        return "", "", []

    title_prompt = f"""你是中英双语新闻编辑。请把下面的英文新闻标题翻译成简体中文。

要求：
- 只输出译文，不要添加任何解释、引号、前缀或后缀
- 保持新闻标题简洁的风格
- 人名、机构名、产品名等专有名词保留英文原文，只翻译其余部分

英文标题：
{title}"""
    zh_title = _ai_call(client, config, title_prompt, max_tokens=300)

    chunks = split_text_by_length(body, max_chars=max_chars)
    zh_chunks = []
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        body_prompt = f"""你是中英双语新闻编辑。请把下面的英文新闻正文片段翻译成简体中文。

要求：
- 这是全文的第 {i}/{total} 块，请直接翻译，不要添加任何解释、标题或前缀
- 忠实原文，不要增删内容，不要概括
- 保持原文的段落结构，原文有换行的地方译文也要有换行
- 如果片段以不完整的句子开头或结尾，也请如实翻译，不要自行补全

英文片段：
{chunk}"""
        translated = _ai_call(client, config, body_prompt, max_tokens=2000)
        if translated:
            zh_chunks.append(translated)
        else:
            print(f"   ⚠️ 第 {i}/{total} 段翻译失败，已跳过")
    zh_body = "\n".join(zh_chunks)

    vocab_prompt = f"""你是英语教学编辑。请从下面的英文新闻中提取 5 个四六级考试常见的高频词汇。

要求：
- 词汇必须是原文中实际出现的，不要凭印象选取
- 优先选择对四六级考生有学习价值的实词（名词、动词、形容词、副词）
- 严格按以下格式输出，每行一个，共 5 行，不要添加任何解释或额外文字：
  英文单词 — 词性缩写. 中文释义
- 词性缩写用 n. / v. / adj. / adv. / prep. / conj. 等

英文标题：
{title}

英文正文：
{body[:3000]}"""
    vocab_raw = _ai_call(client, config, vocab_prompt, max_tokens=500)
    vocab = _parse_vocab(vocab_raw)

    return zh_title, zh_body, vocab


ARTICLE_CSS = """
:root{--bg:#f0f2f5;--card:#fff;--text:#1a1a2e;--muted:#888;--accent:#667eea;--accent2:#764ba2;--ai-bg:#f0f4ff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--text);line-height:1.75;min-height:100vh}
.header{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;padding:26px 30px;border-radius:16px;margin:26px auto 22px;box-shadow:0 6px 24px rgba(102,126,234,.3);max-width:1180px}
.header h1{font-size:1.35em;font-weight:800;letter-spacing:.5px}
.header .subtitle{font-size:.88em;opacity:.88;margin-top:6px}
.back-link{display:inline-block;margin-top:12px;color:#fff;text-decoration:none;font-size:.82em;opacity:.92}
.back-link:hover{text-decoration:underline}
/* 桌面端：默认单栏居中，open 后两栏平分 */
.reader{display:grid;grid-template-columns:minmax(0,1fr);gap:0;align-items:start;max-width:880px;margin:0 auto;padding:0 16px 60px;transition:max-width .32s ease,grid-template-columns .32s ease,gap .32s ease}
.reader.open{grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px;max-width:1440px}
.original-panel{background:var(--card);border-radius:14px;padding:26px 30px;box-shadow:0 2px 8px rgba(0,0,0,.04);min-width:0;transition:padding .32s ease}
.translation-panel{display:none;background:linear-gradient(180deg,#fbfaff,#f4f0ff);border-radius:14px;padding:26px 30px;box-shadow:0 2px 8px rgba(0,0,0,.04);min-width:0;border:1px solid #e8e1ff;transition:padding .32s ease}
.reader.open .translation-panel{display:block;animation:slideIn .24s ease}
@keyframes slideIn{from{opacity:0;transform:translateX(10px)}to{opacity:1;transform:translateX(0)}}
.reader.open .original-panel,.reader.open .translation-panel{padding:22px 26px}
.panel-header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap}
.translate-btn{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;border:none;padding:8px 16px;border-radius:20px;cursor:pointer;font-size:.82em;font-weight:700;box-shadow:0 4px 14px rgba(102,126,234,.32);transition:transform .18s ease,box-shadow .18s ease;white-space:nowrap}
.translate-btn:hover{transform:translateY(-1px);box-shadow:0 6px 18px rgba(102,126,234,.42)}
.translate-btn:active{transform:translateY(0)}
.article-img{max-width:100%;height:auto;border-radius:8px;margin:12px 0;display:block}
.article-title-en{font-weight:750;font-size:1.26em;margin:6px 0 10px;line-height:1.4;color:var(--text)}
.reader.open .article-title-en{font-size:1.14em}
.article-body{white-space:pre-line;font-size:.95em;color:#444;margin:8px 0}
.reader.open .article-body{font-size:.92em}
.article-title-zh{font-weight:750;font-size:1.08em;color:var(--accent2);margin:6px 0 10px;line-height:1.45}
.article-content-zh{white-space:pre-line;font-size:.92em;color:#333;margin:8px 0}
.source-badge{display:inline-block;background:#ede7f6;color:var(--accent2);border-radius:12px;padding:2px 10px;font-weight:600;font-size:.82em}
.article-meta{display:flex;align-items:center;gap:10px;margin-top:14px;font-size:.82em;flex-wrap:wrap;color:var(--muted)}
.vocab-box{background:var(--ai-bg);border-radius:8px;padding:10px 14px;margin-top:14px}
.vocab-box strong{font-size:.88em;color:var(--accent)}
.vocab-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.vocab-item{background:#fff;border:1px solid #e0e0f0;border-radius:14px;padding:3px 10px;font-size:.82em;color:#333}
.footer{text-align:center;color:#999;font-size:.78em;margin-top:0;padding:20px 16px 30px}
.footer a{color:var(--accent)}
/* 固定底部中文浮窗：展开时给英文正文预留不被遮挡的底部空间 */
body.drawer-open .reader{padding-bottom:calc(25vh + 76px)}
.translation-drawer{position:fixed;left:50%;bottom:0;transform:translateX(-50%);z-index:30;width:min(100%,920px);height:25vh;display:flex;flex-direction:column;background:rgba(255,255,255,.97);backdrop-filter:blur(14px);border:1px solid #e8e1ff;border-bottom:0;border-radius:16px 16px 0 0;box-shadow:0 -8px 24px rgba(0,0,0,.12);overflow:hidden;transition:height .28s ease}
.translation-drawer.collapsed{height:46px}
.translation-drawer.collapsed .drawer-content{display:none}
.drawer-handle{display:flex;align-items:center;gap:10px;flex:0 0 46px;width:100%;padding:0 16px;border:0;color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent2));cursor:pointer;font:inherit}
.drawer-grip{width:38px;height:4px;border-radius:999px;background:rgba(255,255,255,.78);flex:none}
.drawer-title{font-weight:750;font-size:.9em;letter-spacing:.2px}
.drawer-toggle-text{margin-left:auto;font-size:.8em;font-weight:700;opacity:.92}
.drawer-content{flex:1;overflow-y:auto;padding:14px 22px 20px}
.drawer-content .article-title-zh{margin:0 0 10px;font-size:1.05em}
.drawer-content .article-content-zh{font-size:.9em;color:#333;line-height:1.72}
.drawer-content .vocab-box{margin-top:14px}
.drawer-content .vocab-box strong{font-size:.82em}
.drawer-content .vocab-item{font-size:.78em}
/* 移动端标签栏：默认隐藏 */
.mobile-tabs{display:none}
/* 桌面端隐藏 translate-btn 的规则不会出现；移动端在媒体查询里隐藏 translate-btn */
@media(max-width:980px){
  /* 移动端：改为标签切换，一次只显示一栏 */
  .mobile-tabs{
    display:flex;gap:6px;margin:0 0 14px;padding:5px;
    background:var(--card);border-radius:14px;
    box-shadow:0 2px 8px rgba(0,0,0,.06);
  }
  .mobile-tabs .tab-btn{
    flex:1;padding:10px 12px;border:none;background:transparent;
    border-radius:10px;font-size:.88em;font-weight:700;color:var(--muted);
    cursor:pointer;transition:all .2s ease;font-family:inherit;letter-spacing:.3px;
  }
  .mobile-tabs .tab-btn.active{
    background:linear-gradient(135deg,var(--accent),var(--accent2));
    color:#fff;box-shadow:0 4px 12px rgba(102,126,234,.32);
  }
  .reader,.reader.open{display:block;max-width:880px;padding:0 16px 40px}
  .reader .original-panel,
  .reader .translation-panel,
  .reader.open .original-panel,
  .reader.open .translation-panel{display:none;animation:none;padding:22px 22px}
  .reader[data-active-tab="original"] .original-panel{display:block}
  .reader[data-active-tab="translation"] .translation-panel{display:block}
  .translate-btn{display:none}
}
@media(max-width:760px){
.header{margin:16px 8px 16px;padding:20px}
.reader,.reader.open{padding:0 8px 40px}
.reader .original-panel,
.reader .translation-panel,
.reader.open .original-panel,
.reader.open .translation-panel{padding:18px}
body.drawer-open .reader{padding-bottom:calc(25vh + 68px)}
.translation-drawer{width:100%;border-left:0;border-right:0;border-radius:16px 16px 0 0}
.drawer-handle{padding:0 12px}
.drawer-content{padding:12px 16px 18px}
}
"""

ARTICLE_JS = """
function toggleTranslation(){
  var reader=document.getElementById('reader');
  var btn=document.getElementById('translateBtn');
  reader.classList.toggle('open');
  if(reader.classList.contains('open')){
    btn.textContent='🇨🇳 收起翻译';
    reader.setAttribute('data-active-tab','translation');
    updateTabs();
  }else{
    btn.textContent='🇨🇳 查看翻译';
  }
}
function switchTab(tab){
  var reader=document.getElementById('reader');
  reader.setAttribute('data-active-tab',tab);
  updateTabs();
}
function toggleDrawer(){
  var drawer=document.getElementById('translationDrawer');
  var btn=document.getElementById('drawerToggle');
  var collapsed=drawer.classList.toggle('collapsed');
  document.body.classList.toggle('drawer-open',!collapsed);
  btn.setAttribute('aria-expanded',String(!collapsed));
  var txt=btn.querySelector('.drawer-toggle-text');
  if(txt){txt.textContent=collapsed?'展开':'收起';}
}
function updateTabs(){
  var reader=document.getElementById('reader');
  var active=reader.getAttribute('data-active-tab')||'original';
  var tabs=document.querySelectorAll('.tab-btn');
  for(var i=0;i<tabs.length;i++){
    if(tabs[i].getAttribute('data-tab')===active){
      tabs[i].classList.add('active');
    }else{
      tabs[i].classList.remove('active');
    }
  }
}
updateTabs();
"""

INDEX_CSS = """
:root{--bg:#f0f2f5;--card:#fff;--text:#1a1a2e;--muted:#888;--accent:#667eea;--accent2:#764ba2}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--text);line-height:1.75;min-height:100vh}
.header{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;padding:32px 36px;border-radius:16px;margin:32px auto 28px;box-shadow:0 6px 24px rgba(102,126,234,.3);max-width:920px}
.header h1{font-size:1.5em;font-weight:800;letter-spacing:.5px}
.header .subtitle{font-size:.88em;opacity:.85;margin-top:6px}
.main{max-width:920px;margin:0 auto;padding:0 16px 60px}
.article{background:var(--card);border-radius:14px;padding:22px 26px;margin-bottom:18px;box-shadow:0 2px 8px rgba(0,0,0,.04);transition:.2s}
.article:hover{box-shadow:0 4px 18px rgba(0,0,0,.08)}
.article-title-en{font-weight:750;font-size:1.15em;margin-bottom:8px;line-height:1.4;color:var(--text)}
.article-img{max-width:100%;height:auto;border-radius:8px;margin:8px 0;display:block}
.article-summary-en{font-size:.92em;color:#444;margin:6px 0}
.article-meta{display:flex;align-items:center;gap:10px;margin-top:12px;font-size:.82em;flex-wrap:wrap}
.source-badge{background:#ede7f6;color:var(--accent2);border-radius:12px;padding:2px 10px;font-weight:600}
.source-link{color:var(--accent);text-decoration:none;font-weight:700}
.source-link:hover{text-decoration:underline}
.footer{text-align:center;color:#999;font-size:.78em;margin-top:32px;padding-top:18px;border-top:1px solid #e0e0e0}
.footer a{color:var(--accent)}
@media(max-width:480px){
.header{margin:16px 8px 20px;padding:22px 20px}
.main{padding:0 8px 40px}
.article{padding:16px}
}
"""


def _image_html(image: str) -> str:
    if not image:
        return ""
    img_url = html_mod.escape(image)
    return f'<img class="article-img" src="{img_url}" alt="" loading="lazy" onerror="this.style.display=\'none\'">'


def _vocab_html(vocab: list) -> str:
    if not vocab:
        return ""
    vocab_items = "\n".join(
        f'<span class="vocab-item">{html_mod.escape(v)}</span>'
        for v in vocab
    )
    return f'<div class="vocab-box"><strong>📚 四六级高频词</strong><div class="vocab-list">{vocab_items}</div></div>'


def build_article_page(article: dict, date_str: str, date_file: str) -> str:
    """生成单篇文章子页：保留原分栏/标签方案，并增加底部中文浮窗"""
    it = article["item"]
    title_en = html_mod.escape(it["title"])
    title_zh = html_mod.escape(article["zh_title"]) if article["zh_title"] else ""
    content_en = html_mod.escape(it.get("content") or it.get("summary") or "")
    content_zh = html_mod.escape(article["zh_content"]) if article["zh_content"] else ""
    source = html_mod.escape(it["source"])
    hub_link = html_mod.escape(f"{date_file}.html")
    img_html = _image_html(it["image"])
    vocab_html = _vocab_html(article["vocab"])
    now_str = now_bj().strftime("%Y-%m-%d %H:%M")
    safe_date = html_mod.escape(date_str)

    translation_heading = f'<h3 class="article-title-zh">{title_zh}</h3>' if title_zh else ""
    translation_body = f'<div class="article-content-zh">{content_zh}</div>' if content_zh else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta property="og:title" content="{safe_date} · {source}">
<meta property="og:description" content="{title_en}">
<meta property="og:type" content="article">
<title>{source} | {safe_date}</title>
<style>{ARTICLE_CSS}</style>
</head>
<body class="drawer-open">
<div class="header">
<h1>📰 晨间双语头条</h1>
<p class="subtitle">{safe_date} · {source}</p>
<a class="back-link" href="{hub_link}">← 返回今日总览</a>
</div>
<main class="reader" id="reader" data-active-tab="original">
<div class="mobile-tabs">
<button class="tab-btn active" data-tab="original" onclick="switchTab('original')">🇬🇧 英文原文</button>
<button class="tab-btn" data-tab="translation" onclick="switchTab('translation')">🇨🇳 中文翻译</button>
</div>
<section class="original-panel">
<div class="panel-header">
<span class="source-badge">{source}</span>
<button class="translate-btn" id="translateBtn" onclick="toggleTranslation()">🇨🇳 查看翻译</button>
</div>
<h2 class="article-title-en">{title_en}</h2>
{img_html}
<div class="article-body">{content_en}</div>
<div class="article-meta">已抓取完整报道并保存在本站</div>
</section>
<section class="translation-panel">
<div class="panel-header">
<span class="source-badge">中文翻译</span>
</div>
{translation_heading}
{translation_body}
{vocab_html}
</section>
</main>
<aside class="translation-drawer" id="translationDrawer" aria-label="中文翻译窗口">
<button class="drawer-handle" id="drawerToggle" type="button" onclick="toggleDrawer()" aria-expanded="true">
<span class="drawer-grip"></span>
<span class="drawer-title">🇨🇳 中文翻译</span>
<span class="drawer-toggle-text">收起</span>
</button>
<div class="drawer-content">
{translation_heading}
{translation_body}
{vocab_html}
</div>
</aside>
<div class="footer">⚡ 自动生成 · <a href="https://github.com/Kalditeen/morning_brief">Kalditeen/morning_brief</a> · {now_str}</div>
<script>{ARTICLE_JS}</script>
</body>
</html>"""


def build_index_page(articles: list, date_str: str, date_file: str) -> str:
    """生成今日总览页"""
    cards = []
    for article in articles:
        it = article["item"]
        title_en = html_mod.escape(it["title"])
        excerpt = (it.get("content") or it.get("summary") or "")[:260]
        summary_en = html_mod.escape(excerpt)
        source = html_mod.escape(it["source"])
        subpage = html_mod.escape(f"{date_file}-{it['cat']}.html")
        img_html = _image_html(it["image"])
        cards.append(f"""<article class="article">
<span class="source-badge">{source}</span>
<h2 class="article-title-en">{title_en}</h2>
{img_html}
<p class="article-summary-en">{summary_en}</p>
<div class="article-meta"><a href="{subpage}" class="source-link">阅读原文 → 进入双语页</a></div>
</article>""")

    cards_html = "\n".join(cards)
    now_str = now_bj().strftime("%Y-%m-%d %H:%M")
    safe_date = html_mod.escape(date_str)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta property="og:title" content="📰 晨间双语头条 | {safe_date}">
<meta property="og:description" content="卫报 1 篇 + BBC 1 篇，领域每日轮换，双语对照，四六级词汇">
<meta property="og:type" content="article">
<title>晨间双语头条 | {safe_date}</title>
<style>{INDEX_CSS}</style>
</head>
<body>
<div class="header">
<h1>📰 晨间双语头条</h1>
<p class="subtitle">{safe_date} · 卫报 1 篇 + BBC 1 篇 · 领域每日轮换</p>
</div>
<main class="main">
{cards_html}
<div class="footer">⚡ 自动生成 · <a href="https://github.com/Kalditeen/morning_brief">Kalditeen/morning_brief</a> · {now_str}</div>
</main>
</body>
</html>"""


def build_wechat_message(articles: list, date_str: str, date_file: str) -> str:
    """微信简报只展示新闻标题和链接，不附带正文内容"""
    divider = "━━━━━━━━━━━━━━━━"
    lines = [
        "📰 **晨间双语头条**",
        f"🗓 {date_str}",
        divider,
    ]
    for idx, article in enumerate(articles, 1):
        it = article["item"]
        subpage_url = f"{CDN_BASE}/{date_file}-{it['cat']}.html"
        source_emoji = "📺" if it.get("cat", "").startswith("bbc") else "🗞️"
        lines.append(f"{source_emoji} **{idx}. {it['title']}**")
        lines.append(f"🔗 [查看双语原文]({subpage_url})")
        lines.append("")
        lines.append(divider)
    return "\n".join(lines)


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

    recent_titles, history = load_recent_titles(docs_dir, days=3)
    print(f"📚 最近 3 天标题去重库: {len(recent_titles)} 条")
    print("\n📡 选择今日 Guardian + BBC 领域（各 1 篇）…")
    guardian_order, bbc_order = daily_source_orders(SOURCES)
    print(f"   Guardian 领域轮换: {' → '.join(source_domain_label(s) for s in guardian_order)}")
    print(f"   BBC 领域轮换:     {' → '.join(source_domain_label(s) for s in bbc_order)}")

    guardian_item = fetch_first_from_group(guardian_order, recent_titles)
    bbc_item = fetch_first_from_group(
        bbc_order,
        recent_titles,
        exclude_domains={source_domain_label(guardian_item)} if guardian_item else None,
    )
    if bbc_item is None:
        bbc_item = fetch_first_from_group(bbc_order, recent_titles)

    items = [it for it in (guardian_item, bbc_item) if it]
    print(f"📊 共采集 {len(items)} 篇")
    if not items:
        raise RuntimeError("未采集到任何新闻")

    print("\n🌐 检查正文抓取结果…")
    for it in items:
        if not it["content"]:
            print(f"   ⚠️ {it['source']} 正文抓取为空，回退到 RSS 摘要")
            it["content"] = it["summary"]
        else:
            print(f"   ✅ {it['source']}: 正文 {len(it['content'])} 字符")

    print("\n🤖 AI 翻译 + 提取高频词汇…")
    articles = []
    for it in items:
        print(f"   📄 处理 {it['source']}: {it['title'][:46]}")
        zh_title, zh_content, vocab = translate_and_vocab(it, config)
        articles.append({
            "item": it,
            "zh_title": zh_title,
            "zh_content": zh_content,
            "vocab": vocab,
        })
        sub_html = build_article_page(articles[-1], date_full, file_date)
        sub_path = os.path.join(docs_dir, f"{file_date}-{it['cat']}.html")
        with open(sub_path, "w") as f:
            f.write(sub_html)
        print(f"   ✅ {sub_path}")

    index_html = build_index_page(articles, date_full, file_date)
    index_path = os.path.join(docs_dir, f"{file_date}.html")
    with open(index_path, "w") as f:
        f.write(index_html)
    print(f"   ✅ {index_path}")

    save_history(docs_dir, history, items, file_date, days=3)

    print("\n💬 推送企业微信…")
    wechat_msg = build_wechat_message(articles, date_full, file_date)
    send_wechat(wechat_msg, config["wechat_webhook"], "日报")

    cleanup_old(docs_dir, 7)
    print("\n📤 提交 HTML 页面…")
    commit_and_push(docs_dir)

    wait_until_beijing(7, 0)

    print("\n" + "=" * 50)
    print("✅ 完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
