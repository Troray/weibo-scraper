import os
import time
import pandas as pd
from datetime import datetime, timedelta
import re
from playwright.sync_api import sync_playwright
from utils import parse_weibo_time

# --- 配置区域 ---
TARGET_USER_ID = "7928198622"  # 替换为目标用户的 ID
START_DATE = "2025-09-01"      # 抓取开始日期
END_DATE = "2025-12-31"        # 抓取结束日期
OUTPUT_DIR = "output"
STATE_FILE = "state.json"
# ----------------

def get_date_ranges(start_date, end_date, step_days=1):
    """
    将大时间段切分为极小的时间段（默认1天），以避免微博搜索结果被截断。
    返回格式: [("2023-01-01", "2023-01-01"), ("2023-01-02", "2023-01-02")...]
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    
    ranges = []
    current = start
    while current <= end:
        current_end = current + timedelta(days=step_days - 1)
        if current_end > end:
            current_end = end
            
        ranges.append((current.strftime("%Y-%m-%d"), current_end.strftime("%Y-%m-%d")))
        current = current_end + timedelta(days=1)
        
    return ranges

def fetch_user_name(page, user_id):
    """
    访问用户微博主页，自动获取用户昵称。
    如果获取失败，回退使用用户 ID。
    """
    profile_url = f"https://weibo.com/u/{user_id}"
    print(f"\n正在获取用户昵称: {profile_url}")
    try:
        page.goto(profile_url)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        # 等待页面动态渲染完成
        time.sleep(3)
        
        # 优先从页面元素获取昵称 (class 名含哈希，使用前缀匹配)
        name_el = page.locator("div[class^='_name_']").first
        if name_el.is_visible():
            user_name = name_el.inner_text().strip()
            if user_name:
                print(f"✅ 获取到用户昵称: {user_name}")
                return user_name
        
        # 备选：尝试从页面标题获取
        title = page.title()
        if title and "_微博" in title:
            user_name = title.split("_微博")[0].strip()
            if user_name:
                print(f"✅ 获取到用户昵称: {user_name}")
                return user_name
    except Exception as e:
        print(f"⚠️ 获取用户昵称失败: {e}")
    
    print(f"⚠️ 无法获取用户昵称，使用用户 ID 作为目录名: {user_id}")
    return user_id

def scrape_weibo_search():
    if not os.path.exists(STATE_FILE):
        print(f"错误: 未找到 {STATE_FILE}。请先运行 login.py 进行登录。")
        return

    # 准备数据存储
    all_posts = []
    processed_ids = set() # 用于去重
    
    # 全局时间范围对象
    global_start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    global_end_dt = datetime.strptime(END_DATE, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    
    # 切分时间段 (改为按天切分)
    date_ranges = get_date_ranges(START_DATE, END_DATE, step_days=1)
    print(f"将抓取任务切分为 {len(date_ranges)} 个时间段 (按天抓取以保证完整性)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        # 自动获取用户昵称
        user_name = fetch_user_name(page, TARGET_USER_ID)

        for start_str, end_str in date_ranges:
            print(f"\n=== 开始抓取时间段: {start_str} 至 {end_str} ===")
            
            # 构造搜索 URL
            # q=uid:123456
            # timescope=custom:YYYY-MM-DD:YYYY-MM-DD
            # Refer=g (高级搜索)
            search_url = (
                f"https://s.weibo.com/weibo?q=uid:{TARGET_USER_ID}"
                f"&typeall=1&suball=1"
                f"&timescope=custom:{start_str}:{end_str}"
                f"&Refer=g"
            )
            
            print(f"访问搜索页: {search_url}")
            page.goto(search_url)
            
            # 处理翻页
            while True:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=30000)
                except:
                    print("页面加载超时，尝试继续...")

                # 检查是否有结果
                # 搜索结果通常在 div.card-wrap 中，或者 div[action-type="feed_list_item"]
                # 搜索页的空状态通常有 "抱歉，未找到相关结果"
                if page.locator("div.card-no-result").is_visible():
                    print("该时间段无数据。")
                    break
                
                # 获取卡片
                cards = page.locator("div[action-type='feed_list_item']").all()
                if not cards:
                    # 备选选择器：有时候搜索结果结构不同
                    cards = page.locator("div.card-wrap").all()
                    # 过滤掉非微博内容的卡片（比如用户信息卡片）
                    # 通常微博内容的卡片会有 mid 属性或者 action-type="feed_list_item"
                    # 这里简单过滤：必须包含 p.txt
                    cards = [c for c in cards if c.locator("p.txt").count() > 0]
                
                print(f"当前页发现 {len(cards)} 条微博")
                
                for card in cards:
                    try:
                        # 1. 解析时间
                        # 搜索页的时间通常在 p.from
                        # 结构: <p class="from"><a href="..." target="_blank">10月04日 19:38</a> 来自 iPhone</p>
                        
                        from_el = card.locator("p.from").first
                        time_str = ""
                        post_link = ""
                        
                        if from_el.is_visible():
                            # 优先尝试从链接获取（因为链接通常包含准确时间或便是详情页链接）
                            time_link_el = from_el.locator("a[target='_blank']").first
                            if not time_link_el.is_visible():
                                time_link_el = from_el.locator("a").first
                            
                            if time_link_el.is_visible():
                                time_str = time_link_el.inner_text().strip()
                                post_link = time_link_el.get_attribute("href")
                            else:
                                # 如果没有链接，尝试直接从 p.from 的文本中提取
                                print("  -> 提示: p.from 中未找到链接，尝试解析文本")
                                raw_text = from_el.inner_text()
                                # 文本可能包含 "10月04日 19:38 来自 iPhone"
                                # 我们取 "来自" 之前的部分，或者直接交给 parse_weibo_time (它现在有正则)
                                time_str = raw_text.split("来自")[0].strip()
                        else:
                            # print("  -> 跳过: 找不到 p.from 元素")
                            # 备选：尝试直接在整个卡片文本中查找日期格式
                            # 常见的搜索页日期格式: "10月04日 19:38", "2023年10月04日", "今天 12:30", "10分钟前"
                            # 我们主要关注绝对日期，因为相对日期通常在 p.from 里
                            
                            card_text = card.inner_text()
                            
                            # 匹配 "10月04日 19:38" 或 "10月4日 19:38"
                            date_match = re.search(r"(\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{1,2})", card_text)
                            if not date_match:
                                # 匹配 "2023年10月04日"
                                date_match = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", card_text)
                            
                            if date_match:
                                time_str = date_match.group(1)
                                print(f"  -> 提示: 通过全文正则找到时间: {time_str}")
                            else:
                                print(f"  -> 跳过: 找不到 p.from 且全文未匹配到日期. 文本片段: {card_text[:50].replace(chr(10), ' ')}...")
                                continue
                            
                        if not time_str:
                            print("  -> 跳过: 时间文本为空")
                            continue

                        # 传入当前时间段的起始日期作为参考，用于推断缺少年份的日期
                        reference_dt = datetime.strptime(start_str, "%Y-%m-%d")
                        post_time = parse_weibo_time(time_str, reference_date=reference_dt)
                        if not post_time:
                            print(f"  -> 跳过: 时间解析失败 '{time_str}'")
                            continue
                            
                        # 2. 提取链接和ID
                        if not post_link:
                             # 如果刚才没获取到链接，尝试找一下
                             if from_el.is_visible():
                                 link_el = from_el.locator("a").first
                                 if link_el.is_visible():
                                     post_link = link_el.get_attribute("href")

                        if post_link and post_link.startswith("//"):
                            post_link = "https:" + post_link
                        
                        # 生成唯一ID
                        if post_link:
                            # 尝试从链接提取 mid
                            # 格式通常是 https://weibo.com/uid/mid
                            post_id = post_link.split("/")[-1].split("?")[0]
                        else:
                            post_id = str(post_time)

                        # --- 去重与过滤 ---
                        if post_id in processed_ids:
                            print(f"  -> 跳过重复微博: {post_id}")
                            continue
                            
                        # 严格时间过滤：确保微博时间在全局配置的范围内
                        # 搜索结果有时会包含置顶微博或推荐内容，时间可能不符
                        if not (global_start_dt <= post_time <= global_end_dt):
                            print(f"  -> 跳过非目标时间段微博: {post_time}")
                            continue
                        # ----------------
                        
                        processed_ids.add(post_id)

                        # 3. 展开全文
                        # 搜索页的展开按钮通常是 a[action-type="fl_unfold"]
                        expand_btn = card.locator("a[action-type='fl_unfold']").first
                        if expand_btn.is_visible():
                            print("点击展开全文...")
                            try:
                                expand_btn.evaluate("el => el.click()")
                                page.wait_for_timeout(500)
                            except:
                                pass
                        
                        # 4. 提取正文
                        # 搜索页正文在 p.txt
                        # 展开后，原来的 p.txt 会隐藏，出现一个新的 p.txt[node-type="feed_list_content_full"]
                        # 或者直接读取 p.txt 的内容（如果没展开）
                        
                        content_full = card.locator("p[node-type='feed_list_content_full']").first
                        content_normal = card.locator("p.txt").first
                        
                        if content_full.is_visible():
                            content = content_full.inner_text()
                        else:
                            content = content_normal.inner_text()
                            
                        # 5. 提取互动数据
                        # div.card-act -> ul -> li
                        footer = card.locator("div.card-act").first
                        stats_text = footer.inner_text().replace("\n", " ").strip() if footer.is_visible() else ""
                        
                        post_data = {
                            "time": post_time,
                            "link": post_link,
                            "content": content,
                            "stats_raw": stats_text
                        }
                        all_posts.append(post_data)
                        print(f"抓取: {post_time} - {content[:10]}...")
                        
                    except Exception as e:
                        print(f"解析出错: {e}")
                        continue
                
                # 寻找下一页
                # ul.s-scroll > li > a.next
                next_btn = page.locator("a.next").first
                if next_btn.is_visible():
                    print("点击下一页...")
                    try:
                        next_btn.click()
                        time.sleep(2) # 等待页面跳转
                    except Exception as e:
                        print(f"翻页失败: {e}")
                        break
                else:
                    print("已到达最后一页。")
                    break
            
            # 每个月抓完，保存一次，防止数据丢失
            save_data(all_posts, user_name)
            # 清空已保存的，避免重复写入（save_data 是全量写入模式，需要调整）
            # 或者我们让 save_data 支持增量，或者我们最后统一保存
            # 为了安全，我们这里先不清空，save_data 每次重写所有数据。
            # 如果数据量特别大，建议改为增量写入。这里数据量应该还好。
            
            # 随机等待，避免频繁请求
            time.sleep(3)

def save_data(data, user_name):
    # 按日期分组
    posts_by_date = {}
    for post in data:
        date_str = post['time'].strftime('%Y-%m-%d')
        if date_str not in posts_by_date:
            posts_by_date[date_str] = []
        posts_by_date[date_str].append(post)
    
    for date_str, posts in posts_by_date.items():
        # 构建目录结构: OUTPUT_DIR/用户名/YYYY-MM/
        month_str = date_str[:7]  # "YYYY-MM"
        month_dir = os.path.join(OUTPUT_DIR, user_name, month_str)
        img_dir = os.path.join(month_dir, "img")
        os.makedirs(month_dir, exist_ok=True)
        os.makedirs(img_dir, exist_ok=True)
        
        file_name = f"{date_str}.md"
        file_path = os.path.join(month_dir, file_name)
        
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"# {date_str} 微博存档\n\n")
                
                # 按时间正序排列
                posts.sort(key=lambda x: x['time'])
                
                for post in posts:
                    time_display = post['time'].strftime('%H:%M:%S')
                    f.write(f"## {time_display}\n\n")
                    f.write(f"[{post['link']}]({post['link']})\n\n")
                    f.write(f"**互动数据:** {post['stats_raw']}\n\n")
                    f.write(f"{post['content']}\n\n")
                    f.write(f"---\n\n")
        except Exception as e:
            print(f"保存文件 {file_name} 失败: {e}")
    
    user_dir = os.path.join(OUTPUT_DIR, user_name)
    print(f"当前已保存 {len(data)} 条数据到 {user_dir}")

if __name__ == "__main__":
    scrape_weibo_search()
