#!/usr/bin/env python3
"""
晨间简报 · GitHub Actions 版
每个工作日 UTC 00:00（北京时间 8:00）自动运行
通知渠道：企业微信群机器人
"""

import os
import re
import json
import html as html_mod
import hashlib
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
from openai import OpenAI

# ── 配置 ──

def load_config():
    config = {}
    config["wechat_webhook"] = os.environ.get("WECHAT_WEBHOOK_URL", "")
    config["openai_api_key"] = os.environ.get("OPENAI_API_KEY", "")
    config["openai_base_url"] = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    config["openai_model"] = os.environ.get("OPENAI_MODEL", "deepseek-v4-pro")
    return config


# ── RSS 源 ──

RSS_SOURCES = [
    {"name": "Google News 中文", "url": "https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "priority": 1},
    {"name": "Google News 财经", "url": "https://news.google.com/rss/search?q=China+stock+market+finance+policy+when:12h&hl=en-US&gl=US&ceid=US:en", "priority": 3},
    {"name": "Google News 中文财经", "url": "https://news.google.com/rss/search?q=%E8%82%A1%E5%B8%82+A%E8%82%A1+%E5%A4%AE%E8%A1%8C+%E6%94%BF%E7%AD%96+%E7%BB%8F%E6%B5%8E&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "priority": 3},
    {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml", "priority": 4},
    {"name": "Google News 国际", "url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", "priority": 5},
]


def fetch_news(hours_back: int = 12) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)
    all_items = []
    seen = set()

    for src in RSS_SOURCES:
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
            if len(title) < 8:
                continue
            h = hashlib.md5(title.encode()).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            summary = html_mod.unescape(getattr(entry, "summary", "") or getattr(entry, "description", ""))
            summary = re.sub(r"<[^>]+>", "", summary)[:300]
            hours_ago = round((now - pub_time).total_seconds() / 3600, 1) if pub_time else None
            all_items.append({
                "title": title,
                "source_name": src["name"],
                "priority": src["priority"],
                "hours_ago": hours_ago,
                "summary": summary.strip(),
            })
            count += 1
        print(f"  ✅ {src['name']}: {count} 条")

    all_items.sort(key=lambda x: (x["priority"], -(x["hours_ago"] or 0)))
    return all_items


def analyze_and_compose(items: list[dict], config: dict) -> str:
    if not config["openai_api_key"]:
        raise RuntimeError("未配置 OPENAI_API_KEY")

    client = OpenAI(api_key=config["openai_api_key"], base_url=config["openai_base_url"])

    news_text = "\n\n".join(
        f"[{i+1}] {item['title']}\n来源: {item['source_name']} | {item['hours_ago']}h前\n摘要: {item['summary'][:200]}"
        for i, item in enumerate(items[:60])
    )

    today = datetime.now()
    wd = ["周一","周二","周三","周四","周五","周六","周日"]
    today_str = f"{today.strftime('%Y年%m月%d日')} {wd[today.weekday()]}"

    system_prompt = f"""你是专业财经晨间简报编辑。北京时间 {today_str} 上午8:00。

请分析以下新闻，输出**企业微信 Markdown 格式**的简报。要求：

1. 用 # 标题开头：「📰 晨间简报 | {today_str}」
2. ## 今日要闻 筛选 3-5 条最重要事件，每条一行加粗
3. ## 🌍 国际 按重要性排列，格式：
   > ✅/⚠️/🔄 **标题** — 来源 · Xh前
   > 摘要（一句话）
4. 同样的格式写 ## 🇨🇳 国内、## 📈 市场财经、## 🤖 科技AI、## 💡 值得关注
5. 末尾 ## 📊 统计：共X条、多方验证率X%、信源列表
6. 总字数控制在 3500 以内

验证状态：✅多方确认 ⚠️单源 🔄持续中。不编造新闻，不确定的标注「未经确认」。"""

    response = client.chat.completions.create(
        model=config["openai_model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"过去12小时新闻：\n\n{news_text}"}
        ],
        temperature=0.7,
        max_tokens=3500,
    )
    return response.choices[0].message.content


def send_wechat(markdown: str, webhook_url: str):
    """企业微信群机器人 Markdown 推送"""
    payload = json.dumps({
        "msgtype": "markdown",
        "markdown": {"content": markdown}
    }).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
        if result.get("errcode") != 0:
            raise RuntimeError(f"企业微信推送失败: {result}")
        print(f"✅ 简报已推送至企业微信")


def main():
    print("=" * 50)
    print("📰 晨间简报 · 开始运行")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (UTC)")
    print("=" * 50)

    config = load_config()

    missing = []
    if not config["wechat_webhook"]:
        missing.append("WECHAT_WEBHOOK_URL")
    if not config["openai_api_key"]:
        missing.append("OPENAI_API_KEY")
    if missing:
        raise RuntimeError(f"缺少配置: {', '.join(missing)}")

    print("\n📡 采集新闻中...")
    items = fetch_news(hours_back=12)
    if not items:
        raise RuntimeError("未获取到任何新闻")
    print(f"\n📊 共采集 {len(items)} 条新闻")

    print("\n🤖 LLM 分析中...")
    markdown = analyze_and_compose(items, config)
    print(f"   内容长度: {len(markdown)} 字符")

    print("\n📨 推送企业微信中...")
    send_wechat(markdown, config["wechat_webhook"])

    print("\n" + "=" * 50)
    print("✅ 晨间简报完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
