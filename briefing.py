#!/usr/bin/env python3
"""
晨间简报 · GitHub Actions 版
周一至周五：AI 产业动态 + 软件工程就业
周五附加：本周学术论文 & 保研特辑
通知渠道：企业微信群机器人
"""

import os
import re
import json
import html as html_mod
import hashlib
import urllib.request
from datetime import datetime, timezone, timedelta

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


# ── 源定义 ──

DAILY_SOURCES = [
    {"name": "Hacker News 热榜", "url": "https://hnrss.org/frontpage?count=15", "category": "AI 产业"},
    {"name": "V2EX 热帖", "url": "https://www.v2ex.com/feed/tab/hot.xml", "category": "就业"},
    {"name": "36氪", "url": "https://36kr.com/feed", "category": "就业"},
    {"name": "Google News AI", "url": "https://news.google.com/rss/search?q=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+AI+%E5%A4%A7%E6%A8%A1%E5%9E%8B+when:24h&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "category": "AI 产业"},
    {"name": "Google News 就业", "url": "https://news.google.com/rss/search?q=%E4%BA%92%E8%81%94%E7%BD%91+%E6%8B%9B%E8%81%98+%E8%A3%81%E5%91%98+%E9%9D%A2%E7%BB%8F+%E7%A8%8B%E5%BA%8F%E5%91%98+%E5%B0%B1%E4%B8%9A&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "category": "就业"},
]

FRIDAY_SOURCES = [
    {"name": "ArXiv cs.AI", "url": "http://export.arxiv.org/rss/cs.AI", "category": "学术"},
    {"name": "ArXiv cs.CL", "url": "http://export.arxiv.org/rss/cs.CL", "category": "学术"},
    {"name": "Reddit ML", "url": "https://www.reddit.com/r/MachineLearning/.rss", "category": "学术"},
    {"name": "Google News 保研", "url": "https://news.google.com/rss/search?q=%E4%BF%9D%E7%A0%94+%E5%A4%8F%E4%BB%A4%E8%90%A5+%E6%8E%A8%E5%85%8D+site:zhihu.com&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "category": "保研"},
    {"name": "Google News 保研2", "url": "https://news.google.com/rss/search?q=%E4%BF%9D%E7%A0%94+%E5%A4%8F%E4%BB%A4%E8%90%A5+%E6%8E%A8%E5%85%8D+%E7%A0%94%E7%A9%B6%E7%94%9F%E6%8B%9B%E7%94%9F&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "category": "保研"},
    {"name": "Google News 校招", "url": "https://news.google.com/rss/search?q=%E8%BD%AF%E4%BB%B6%E5%B7%A5%E7%A8%8B+%E8%AE%A1%E7%AE%97%E6%9C%BA+%E6%A0%A1%E6%8B%9B+%E5%AE%9E%E4%B9%A0&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "category": "保研/就业"},
]


def fetch_news(sources: list, hours_back: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)
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
            pub_time = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_time = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                pub_time = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            if pub_time and pub_time < cutoff:
                continue
            title = html_mod.unescape(getattr(entry, "title", "").strip())
            if len(title) < 6:
                continue
            h = hashlib.md5((src["category"] + title).encode()).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            summary = html_mod.unescape(getattr(entry, "summary", "") or getattr(entry, "description", ""))
            summary = re.sub(r"<[^>]+>", "", summary)[:400]
            hours_ago = round((now - pub_time).total_seconds() / 3600, 1) if pub_time else None
            all_items.append({
                "title": title,
                "source_name": src["name"],
                "category": src["category"],
                "hours_ago": hours_ago,
                "summary": summary.strip(),
            })
            count += 1
        print(f"  ✅ {src['name']}: {count} 条")

    all_items.sort(key=lambda x: -(x["hours_ago"] or 0), reverse=True)
    return all_items


def compose_daily(items: list[dict], config: dict) -> str:
    client = OpenAI(api_key=config["openai_api_key"], base_url=config["openai_base_url"])

    news_text = "\n\n".join(
        f"[{i+1}] [{item['category']}] {item['title']}\n来源: {item['source_name']} | {item['hours_ago']}h前"
        for i, item in enumerate(items[:50])
    )

    today = datetime.now()
    wd = ["周一","周二","周三","周四","周五","周六","周日"]
    today_str = f"{today.strftime('%Y年%m月%d日')} {wd[today.weekday()]}"
    is_friday = today.weekday() == 4

    prompt = f"""你是软件工程本科生的AI & 就业晨间助手。北京时间 {today_str} 上午8:00。

输出精简日报，面向计算机/软件工程本科生。用 `===(板块)` 分隔，不要写「标题」二字，直接写内容。严格照下面格式：

📰 晨间简报 | {today_str}

===

🤖 AI 产业
**一句话：**（今天AI领域最重要的一件事，务必具体，不要泛泛而谈）

· OpenAI发布GPT-5 API价格降至每百万token 0.15美元 — 推理成本较上代降低70%
  → 启示：个人开发者做AI应用的门槛断崖式下降，用API比自建模型划算

· Meta开源Llama 4全系列8B到405B权重 — 支持多语言且推理性能翻倍
  → 启示：本地跑大模型做毕设/竞赛完全可行，Macbook就能跑8B

（每条必须：· 具体事件 — 具体数据或细节 → 启示：具体可操作的建议。禁止“学习某知识是必备技能”这种废话。最多8条）

===

💼 就业水温
**一句话：**（今天就业市场的核心信号，务必具体，不要泛泛而谈）

· 字节跳动2027届AI岗提前批开放 — 要求有大模型微调或RLHF项目经验
  → 启示：简历里放一个fine-tune项目是今年校招硬通货

（每条必须：· 具体事件 → 启示：具体可操作的建议。禁止泛泛的“了解行业动态”。最多6条）

===

📊 {len(items)}条 · HN / V2EX / 36氪 / Google News

⚠️ 每条的启示必须具体、可操作。绝对禁止「学习XX知识是软件工程师必备技能」「了解XX是就业市场必备技能」这类废话模板。如果你只能写出这种废话，就不要写那条新闻。总字数 1800 内。"""

    response = client.chat.completions.create(
        model=config["openai_model"],
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": f"新闻：\n\n{news_text}"}],
        temperature=0.7, max_tokens=2400,
    )
    return response.choices[0].message.content


def compose_friday_special(items: list[dict], config: dict) -> str:
    client = OpenAI(api_key=config["openai_api_key"], base_url=config["openai_base_url"])

    news_text = "\n\n".join(
        f"[{i+1}] [{item['category']}] {item['title']}\n来源: {item['source_name']} | {item['hours_ago']}h前\n摘要: {item['summary'][:300]}"
        for i, item in enumerate(items[:60])
    )

    today = datetime.now()
    wd = ["周一","周二","周三","周四","周五","周六","周日"]
    today_str = f"{today.strftime('%Y年%m月%d日')} {wd[today.weekday()]}"

    prompt = f"""你是软件工程本科生的学术 & 保研信息助手。北京时间 {today_str}。

请输出**企业微信 Markdown** 周五特辑。要求：

## 🎓 本周学术 & 保研特辑 | {today_str}

## 📄 本周值得关注的论文（5-8篇）
- 每条含：英文原标题 + **一句中文摘要**（用自己的话概括这篇论文做了什么、为什么重要）
- 面向本科生理解水平，不要堆砌术语
- 优先选有开源代码或 demo 的

## 🏫 保研 & 校招动态（本周）
- 夏令营通知、推免政策变化、院校招生动态
- 重点院校计算机/软件方向

## 📊 特辑统计 · 学术X篇 + 保研/就业Y条

不编造。总字数严格控制在 2800 内。"""

    response = client.chat.completions.create(
        model=config["openai_model"],
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": f"本周学术 & 保研信息：\n\n{news_text}"}],
        temperature=0.7, max_tokens=3200,
    )
    return response.choices[0].message.content


def send_wechat(markdown: str, webhook_url: str, label: str = ""):
    if len(markdown) > 4000:
        markdown = markdown[:4000] + "\n\n> ⚠️ 内容过长已截断"
        print(f"   ⚠️ 已截断至 {len(markdown)} 字符")
    payload = json.dumps({
        "msgtype": "markdown",
        "markdown": {"content": markdown}
    }).encode("utf-8")

    req = urllib.request.Request(webhook_url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")

    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
        if result.get("errcode") != 0:
            raise RuntimeError(f"推送失败: {result}")
        print(f"✅ {label} 已推送")


def main():
    print("=" * 50)
    print("📰 晨间简报 · 开始运行")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (UTC)")
    print("=" * 50)

    config = load_config()
    missing = [k for k in ["wechat_webhook", "openai_api_key"] if not config[k]]
    if missing:
        raise RuntimeError(f"缺少配置: {', '.join(missing)}")

    is_friday = datetime.now().weekday() == 4

    # ── 日报部分 ──
    print("\n📡 [日报] 采集 AI + 就业新闻（过去24h）...")
    daily_items = fetch_news(DAILY_SOURCES, hours_back=24)
    print(f"📊 日报采集 {len(daily_items)} 条")

    print("\n🤖 生成日报...")
    daily_md = compose_daily(daily_items, config)
    print(f"   日报长度: {len(daily_md)} 字符")

    print("\n📨 推送日报...")
    send_wechat(daily_md, config["wechat_webhook"], "日报")

    # ── 周五特辑 ──
    if is_friday:
        print("\n📡 [周五特辑] 采集学术 + 保研信息（过去一周）...")
        friday_items = fetch_news(FRIDAY_SOURCES, hours_back=24 * 7)
        print(f"📊 特辑采集 {len(friday_items)} 条")

        print("\n🤖 生成周五特辑...")
        special_md = compose_friday_special(friday_items, config)
        print(f"   特辑长度: {len(special_md)} 字符")

        print("\n📨 推送特辑...")
        send_wechat(special_md, config["wechat_webhook"], "周五特辑")

    print("\n" + "=" * 50)
    print("✅ 晨间简报完成" + ("（含周五特辑）" if is_friday else ""))
    print("=" * 50)


if __name__ == "__main__":
    main()
