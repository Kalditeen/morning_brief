#!/usr/bin/env python3
"""
晨间简报 · GitHub Actions 版
每个工作日 UTC 00:00（北京时间 8:00）自动运行
"""

import os
import re
import json
import html as html_mod
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import feedparser
from openai import OpenAI

# ── 配置：从环境变量读取（GitHub Secrets），本地回退到 .briefing_config.json ──

def load_config():
    """优先环境变量，其次本地配置文件"""
    config = {}

    # Email
    config["email_sender"] = os.environ.get("BRIEFING_EMAIL_SENDER", "")
    config["email_password"] = os.environ.get("BRIEFING_EMAIL_PASSWORD", "")
    config["email_recipient"] = os.environ.get("BRIEFING_EMAIL_RECIPIENT", "")
    config["smtp_server"] = os.environ.get("BRIEFING_SMTP_SERVER", "smtp.qq.com")
    config["smtp_port"] = int(os.environ.get("BRIEFING_SMTP_PORT") or "587")

    # LLM
    config["openai_api_key"] = os.environ.get("OPENAI_API_KEY", "")
    config["openai_base_url"] = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    config["openai_model"] = os.environ.get("OPENAI_MODEL", "gpt-4.1")

    # Fallback to local config file
    local_path = Path(".briefing_config.json")
    if local_path.exists():
        with open(local_path) as f:
            local = json.load(f).get("email", {})
        config["email_sender"] = config["email_sender"] or local.get("sender", "")
        config["email_password"] = config["email_password"] or local.get("password", "")
        config["email_recipient"] = config["email_recipient"] or local.get("recipient", "")
        config["smtp_server"] = config["smtp_server"] if os.environ.get("BRIEFING_SMTP_SERVER") else local.get("smtp_server", config["smtp_server"])
        config["smtp_port"] = config["smtp_port"] if os.environ.get("BRIEFING_SMTP_PORT") else local.get("smtp_port", config["smtp_port"])

    return config


# ── RSS 源定义 ──

RSS_SOURCES = [
    # 优先级1：新华社 / 国内政策
    {
        "name": "Google News 中文",
        "url": "https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "priority": 1,
        "category": "domestic"
    },
    # 优先级3：Bloomberg/Reuters 视角
    {
        "name": "Google News 财经",
        "url": "https://news.google.com/rss/search?q=China+stock+market+finance+policy+when:12h&hl=en-US&gl=US&ceid=US:en",
        "priority": 3,
        "category": "finance"
    },
    {
        "name": "Google News 中文财经",
        "url": "https://news.google.com/rss/search?q=%E8%82%A1%E5%B8%82+A%E8%82%A1+%E5%A4%AE%E8%A1%8C+%E6%94%BF%E7%AD%96+%E7%BB%8F%E6%B5%8E&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "priority": 3,
        "category": "finance"
    },
    # 国际视角
    {
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "priority": 4,
        "category": "international"
    },
    # 补漏
    {
        "name": "Google News 国际",
        "url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
        "priority": 5,
        "category": "international"
    },
]


def fetch_news(hours_back: int = 12) -> list[dict]:
    """抓取所有RSS源，过滤过去N小时内的新闻，去重"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)
    all_items = []
    seen_hashes = set()

    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
        except Exception as e:
            print(f"  ⚠️ 无法获取 {source['name']}: {e}")
            continue

        count = 0
        for entry in feed.entries:
            # 解析时间
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

            # 简单去重
            h = hashlib.md5(title.encode()).hexdigest()
            if h in seen_hashes:
                continue
            seen_hashes.add(h)

            summary = html_mod.unescape(getattr(entry, "summary", "") or getattr(entry, "description", ""))
            summary = re.sub(r"<[^>]+>", "", summary)[:300]

            hours_ago = round((now - pub_time).total_seconds() / 3600, 1) if pub_time else None

            all_items.append({
                "title": title,
                "source_name": source["name"],
                "priority": source["priority"],
                "category": source["category"],
                "hours_ago": hours_ago,
                "summary": summary.strip(),
                "pub_time": pub_time.isoformat() if pub_time else "",
            })
            count += 1

        print(f"  ✅ {source['name']}: {count} 条")

    # 按优先级和时间排序
    all_items.sort(key=lambda x: (x["priority"], -(x["hours_ago"] or 0)))
    return all_items


def analyze_and_compose(items: list[dict], config: dict) -> str:
    """调用 LLM 分析新闻、交叉验证、撰写 HTML 简报"""
    if not config["openai_api_key"]:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法调用 LLM 分析")

    client = OpenAI(
        api_key=config["openai_api_key"],
        base_url=config["openai_base_url"],
    )

    # 构建新闻列表供 LLM 分析
    news_text = "\n\n".join(
        f"[{i+1}] 【{item['category']}·优先级{item['priority']}】{item['title']}\n"
        f"来源: {item['source_name']} | {item['hours_ago']}小时前\n"
        f"摘要: {item['summary'][:200]}"
        for i, item in enumerate(items[:60])
    )

    today = datetime.now()
    weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    today_str = f"{today.strftime('%Y年%m月%d日')} {weekday_map[today.weekday()]}"

    system_prompt = f"""你是专业的财经晨间简报编辑。当前时间：北京时间 {today_str} 上午8:00。

请根据提供的新闻列表，完成以下任务并用 HTML 格式输出邮件正文：

1. 筛选最重要的 3-5 条作为「今日要闻概览」
2. 将所有新闻归类到：🌍国际新闻、🇨🇳国内要闻、📈市场与财经、🤖科技AI动态、💡值得关注
3. 对重大事件交叉验证：同一事件被多个来源报道的标注 ✅多方确认，单源的标注 ⚠️单源报道，发展中的标注 🔄持续发展中
4. 每条新闻格式：验证状态 + 标题 + 来源 + 时间 + 1-2句摘要
5. 最后附上简报统计（采集数、验证率、信源列表）

HTML 样式要求：
- 使用内联CSS，字体 PingFang SC / Microsoft YaHei
- ✅ 绿色背景卡片、⚠️ 橙色背景卡片、🔄 蓝色背景卡片
- 每个板块用左侧彩色竖线区分
- 简洁专业，适合邮件阅读

只输出 <body> 内的 HTML，不要包含 <!DOCTYPE> 或 <html> 标签。"""

    response = client.chat.completions.create(
        model=config["openai_model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"以下是过去12小时采集的新闻，请分析并生成简报：\n\n{news_text}"}
        ],
        temperature=0.7,
        max_tokens=4000,
    )

    return response.choices[0].message.content


def send_email(html_content: str, config: dict):
    """通过 SMTP 发送邮件"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.header import Header

    today = datetime.now()
    wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 640px; margin: 0 auto; padding: 20px; background: #fff; color: #222; line-height: 1.7;">
<div style="text-align: center; padding: 24px 0 16px;">
<h1 style="margin:0 0 4px;font-size:22px;">📰 晨间简报</h1>
<p style="margin:0;color:#888;font-size:13px;">{today.strftime('%Y年%m月%d日')} {wd[today.weekday()]} · 北京时间 8:00</p>
</div>
<hr style="border:none;border-top:2px solid #1a1a1a;margin:0 0 20px;">
{html_content}
<hr style="border:none;border-top:1px solid #e0e0e0;margin:16px 0;">
<p style="font-size:11px;color:#aaa;text-align:center;margin:0;">
⚡ 自动生成于 {today.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）<br>
Codex 晨间简报 · GitHub Actions · 工作日 8:00 推送
</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(f"晨间简报 | {today.strftime('%Y年%m月%d日')} {wd[today.weekday()]}", "utf-8")
    msg["From"] = config["email_sender"]
    msg["To"] = config["email_recipient"]
    msg.attach(MIMEText(full_html, "html", "utf-8"))

    with smtplib.SMTP(config["smtp_server"], config["smtp_port"], timeout=30) as server:
        server.starttls()
        server.login(config["email_sender"], config["email_password"])
        server.send_message(msg)

    print(f"✅ 简报已发送至 {config['email_recipient']}")


def main():
    print("=" * 50)
    print("📰 晨间简报 · 开始运行")
    print(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (UTC)")
    print("=" * 50)

    config = load_config()

    # 验证配置
    missing = []
    if not config["email_sender"]:
        missing.append("BRIEFING_EMAIL_SENDER")
    if not config["email_password"]:
        missing.append("BRIEFING_EMAIL_PASSWORD")
    if not config["email_recipient"]:
        missing.append("BRIEFING_EMAIL_RECIPIENT")
    if not config["openai_api_key"]:
        missing.append("OPENAI_API_KEY")
    if missing:
        raise RuntimeError(f"缺少必要配置: {', '.join(missing)}")

    # Step 1: 抓取新闻
    print("\n📡 采集新闻中...")
    items = fetch_news(hours_back=12)
    if not items:
        raise RuntimeError("未获取到任何新闻")
    print(f"\n📊 共采集 {len(items)} 条新闻")

    # Step 2: LLM 分析 + 撰写
    print("\n🤖 LLM 分析中...")
    html_content = analyze_and_compose(items, config)
    print(f"   HTML 长度: {len(html_content)} 字符")

    # Step 3: 发送
    print("\n📧 发送邮件中...")
    send_email(html_content, config)

    print("\n" + "=" * 50)
    print("✅ 晨间简报完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
