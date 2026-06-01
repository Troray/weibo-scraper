import os
import sys
import time
import pandas as pd
from datetime import datetime, timedelta
import re
from playwright.sync_api import sync_playwright
from utils import parse_weibo_time

# 解决 Windows 终端下 print 打印 Emoji 表情时的 GBK 编码报错问题
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')


import config

# --- 映射统一配置文件 config.py 中的设置 ---
TARGET_USER_IDS = config.TARGET_USER_IDS
START_DATE = config.START_DATE
END_DATE = config.END_DATE
OUTPUT_DIR = config.OUTPUT_DIR
STATE_FILE = config.STATE_FILE
ENABLE_SAVE_IMAGES = config.ENABLE_SAVE_IMAGES
ENABLE_SAVE_VIDEOS = config.ENABLE_SAVE_VIDEOS
ENABLE_SAVE_LIVEPHOTOS = config.ENABLE_SAVE_LIVEPHOTOS
ENABLE_SAVE_CSV = config.ENABLE_SAVE_CSV
ENABLE_SAVE_SQLITE = config.ENABLE_SAVE_SQLITE
ENABLE_SAVE_MARKDOWN = config.ENABLE_SAVE_MARKDOWN
ONLY_ORIGINAL = config.ONLY_ORIGINAL
INCREMENTAL_LOOKBACK_DAYS = getattr(config, "INCREMENTAL_LOOKBACK_DAYS", 5)
# ----------------


def get_user_ids(config_val):
    """
    解析配置的用户 ID，支持单个 ID、ID 列表或文本文件路径。
    返回格式: [{"id": user_id, "username": username_or_None, "start_time": datetime_or_None}]
    """
    raw_list = []
    if isinstance(config_val, (list, tuple, set)):
        raw_list = [str(uid).strip() for uid in config_val if str(uid).strip()]
    elif isinstance(config_val, str):
        config_val = config_val.strip()
        if config_val.endswith(".txt"):
            if os.path.exists(config_val):
                with open(config_val, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            raw_list.append(line)
            else:
                print(f"⚠️ 警告: 找不到用户 ID 配置文件 '{config_val}'，将其作为单个 ID 处理。")
                raw_list = [config_val]
        else:
            raw_list = [config_val]
    elif config_val is not None:
        raw_list = [str(config_val).strip()]

    users = []
    for item in raw_list:
        parts = item.split()
        if not parts:
            continue
        user_id = parts[0]
        username = None
        start_time = None
        
        # 解析剩余部分的字段
        for part in parts[1:]:
            # 尝试解析为 ISO 格式时间: YYYY-MM-DDTHH:MM:SS
            try:
                start_time = datetime.strptime(part, "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                # 无法解析为时间，则作为用户备注名
                username = part
                
        users.append({
            "id": user_id,
            "username": username,
            "start_time": start_time
        })
    return users


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


def parse_weibo_stats(stats_text):
    """
    解析微博的转发、评论、点赞数量。
    返回: (reposts, comments, attitudes)
    """
    if not stats_text:
        return 0, 0, 0

    # 规范化空格
    stats_text = re.sub(r'\s+', ' ', stats_text).strip()

    # 提取数字工具函数，例如 "1.5万" -> 15000， "342" -> 342
    def parse_num(num_str):
        if not num_str:
            return 0
        if '万' in num_str:
            try:
                val = float(num_str.replace('万', '').strip())
                return int(val * 10000)
            except:
                return 0
        try:
            return int(num_str)
        except:
            return 0

    # 检查是否是纯数字和空格（例如旧格式的 stats_raw "17 342 1621"）
    parts = stats_text.split()
    pure_numbers = []
    for p in parts:
        if re.match(r'^\d+(\.\d+)?万?$', p):
            pure_numbers.append(p)

    if len(pure_numbers) == 3:
        return parse_num(pure_numbers[0]), parse_num(pure_numbers[1]), parse_num(pure_numbers[2])

    # 如果有关键词
    reposts = 0
    comments = 0
    likes = 0

    repost_match = re.search(r'(?:转发|转评|共转)\s*(\d+(?:\.\d+)?万?)', stats_text)
    if repost_match:
        reposts = parse_num(repost_match.group(1))

    comment_match = re.search(r'评论\s*(\d+(?:\.\d+)?万?)', stats_text)
    if comment_match:
        comments = parse_num(comment_match.group(1))

    like_match = re.search(r'(?:点赞|赞|态度)\s*(\d+(?:\.\d+)?万?)', stats_text)
    if like_match:
        likes = parse_num(like_match.group(1))

    # 如果都没有匹配到，但有 2 个纯数字，默认为 转发 评论 0
    if reposts == 0 and comments == 0 and likes == 0:
        if len(pure_numbers) == 2:
            return parse_num(pure_numbers[0]), parse_num(pure_numbers[1]), 0
        elif len(pure_numbers) == 1:
            return 0, 0, parse_num(pure_numbers[0])

    return reposts, comments, likes


def download_file(url, save_path, page=None):
    """
    流式分块下载函数，支持多线程下载、实时进度显示、网速监控和超时处理。
    """
    import urllib.request
    import threading
    import sys
    import time
    
    headers = {
        "Referer": "https://video.weibo.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    def print_progress(downloaded, total, speed=0.0):
        if total <= 0:
            sys.stdout.write(f"\r正在下载: {downloaded / 1024 / 1024:.2f} MB...")
        else:
            percent = (downloaded / total) * 100
            bar_length = 30
            filled_length = int(bar_length * downloaded // total)
            bar = '█' * filled_length + '░' * (bar_length - filled_length)
            speed_str = f" - {speed:.1f} MB/s" if speed > 0 else ""
            sys.stdout.write(
                f"\r下载进度: [{bar}] {percent:.1f}% "
                f"({downloaded / 1024 / 1024:.2f}MB / {total / 1024 / 1024:.2f}MB){speed_str}"
            )
        sys.stdout.flush()

    total_size = 0
    support_ranges = False
    try:
        req = urllib.request.Request(url, headers=headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as res:
            content_length = res.getheader('Content-Length')
            if content_length:
                total_size = int(content_length)
            accept_ranges = res.getheader('Accept-Ranges')
            if accept_ranges and accept_ranges.lower() == 'bytes':
                support_ranges = True
    except Exception:
        try:
            req_get = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-0"})
            with urllib.request.urlopen(req_get, timeout=15) as res:
                content_range = res.getheader('Content-Range')
                if content_range:
                    total_size = int(content_range.split('/')[-1])
                    support_ranges = True
        except Exception:
            pass

    # 大于 15MB 且支持 Range 请求时使用 5 线程下载
    MIN_MULTIPART_SIZE = 15 * 1024 * 1024
    NUM_THREADS = 5
    
    if support_ranges and total_size > MIN_MULTIPART_SIZE:
        print(f"检测到文件大小: {total_size / 1024 / 1024:.2f} MB，将使用 {NUM_THREADS} 线程并行下载...")
        try:
            with open(save_path, "wb") as f:
                f.truncate(total_size)
        except Exception:
            support_ranges = False
            
        if support_ranges:
            part_size = total_size // NUM_THREADS
            threads = []
            downloaded_bytes = [0] * NUM_THREADS
            errors = []
            lock = threading.Lock()
            
            def download_part(thread_idx, start_pos, end_pos):
                part_headers = {**headers, "Range": f"bytes={start_pos}-{end_pos}"}
                try:
                    req_part = urllib.request.Request(url, headers=part_headers)
                    with urllib.request.urlopen(req_part, timeout=30) as conn:
                        buffer_size = 256 * 1024
                        current_pos = start_pos
                        while True:
                            chunk = conn.read(buffer_size)
                            if not chunk:
                                break
                            
                            with lock:
                                with open(save_path, "r+b") as f:
                                    f.seek(current_pos)
                                    f.write(chunk)
                                    
                            current_pos += len(chunk)
                            downloaded_bytes[thread_idx] += len(chunk)
                except Exception as ex:
                    errors.append(ex)
            
            start_time = time.time()
            for i in range(NUM_THREADS):
                start_pos = i * part_size
                end_pos = (i + 1) * part_size - 1 if i < NUM_THREADS - 1 else total_size - 1
                t = threading.Thread(target=download_part, args=(i, start_pos, end_pos))
                threads.append(t)
                t.start()
                
            while any(t.is_alive() for t in threads):
                current_downloaded = sum(downloaded_bytes)
                elapsed = time.time() - start_time
                speed = (current_downloaded / 1024 / 1024) / elapsed if elapsed > 0 else 0.0
                print_progress(current_downloaded, total_size, speed)
                time.sleep(0.5)
                
            for t in threads:
                t.join()
                
            if not errors and sum(downloaded_bytes) == total_size:
                print_progress(total_size, total_size)
                print("\n✅ 多线程下载完成。")
                return True
            else:
                print(f"\n⚠️ 多线程下载失败，已降级为单线程流式下载。错误: {errors}")
                
    # 2. 降级为单线程流式下载
    print(f"开始单线程流式下载...")
    try:
        req = urllib.request.Request(url, headers=headers)
        start_time = time.time()
        with urllib.request.urlopen(req, timeout=30) as conn:
            content_length = conn.getheader('Content-Length')
            total = int(content_length) if content_length else total_size
            downloaded = 0
            buffer_size = 1024 * 1024  # 1MB 缓存
            
            with open(save_path, "wb") as f:
                while True:
                    chunk = conn.read(buffer_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    elapsed = time.time() - start_time
                    speed = (downloaded / 1024 / 1024) / elapsed if elapsed > 0 else 0.0
                    print_progress(downloaded, total, speed)
                    
            print_progress(downloaded, total)
            print("\n✅ 下载完成。")
            return True
    except Exception as e:
        print(f"\n❌ 下载失败: {url}, 错误: {e}")
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except Exception:
                pass
        return False

def extract_high_quality_video(detail_url, headless_browser):
    if not detail_url:
        return ""
    
    if detail_url.startswith("//"):
        detail_url = "https:" + detail_url
        
    print(f"正在获取高清直链: {detail_url}")
    
    best_url = ""
    video_page = None
    context = None
    try:
        context = headless_browser.new_context(storage_state=STATE_FILE)
        video_page = context.new_page()
        
        video_page.goto(detail_url)
        try:
            video_page.wait_for_load_state("domcontentloaded", timeout=12000)
            video_page.wait_for_selector("video", timeout=8000)
        except Exception:
            pass
            
        time.sleep(2.5) # 给 JS 渲染与数据填充留时间
        
        candidate_urls = []
        
        # 1. 动态 DOM 中 <video> 标签当前的 src
        try:
            video_element = video_page.locator("video").first
            if video_element.is_visible():
                src = video_element.get_attribute("src")
                if src:
                    candidate_urls.append(src)
        except Exception:
            pass
            
        # 2. 页面 HTML 源代码中包含的所有新浪视频 CDN 直链
        try:
            page_content = video_page.content()
            found_urls = re.findall(r'//\w+\.video\.weibocdn\.com/[^\'"\s>]+', page_content)
            for f_url in found_urls:
                candidate_urls.append("https:" + f_url if f_url.startswith("//") else f_url)
        except Exception as e:
            print(f"提取源码直链出错: {e}")
            
        # 过滤并清洗链接
        valid_urls = []
        for url in candidate_urls:
            import html
            cleaned_url = html.unescape(url).strip()
            cleaned_url = cleaned_url.strip('\'" \t\n\r>\\')
            if ".mp4" in cleaned_url and cleaned_url not in valid_urls:
                valid_urls.append(cleaned_url)
                
        if valid_urls:
            # 分辨率打分排序
            def get_url_score(url):
                score = 0
                if "mp4_2k" in url or "2k" in url.lower():
                    score = 50
                elif "1080p" in url or "1920x1080" in url:
                    score = 40
                elif "720p" in url or "1280x720" in url:
                    score = 30
                elif "480p" in url or "852x480" in url or "mp4_hd" in url:
                    score = 20
                elif "360p" in url or "640x360" in url or "mp4_ld" in url:
                    score = 10
                return score
                
            valid_urls.sort(key=get_url_score, reverse=True)
            best_url = valid_urls[0]
            
            best_score = get_url_score(best_url)
            quality_map = {50: "2K/4K", 40: "1080p", 30: "720p", 20: "480p", 10: "360p", 0: "未知"}
            print(f"解析到最高清晰度级别: {quality_map.get(best_score, '未知')} -> {best_url[:80]}...")
            
    except Exception as e:
        print(f"静默解析详情页高清直链失败: {e}")
    finally:
        try:
            if video_page:
                video_page.close()
            if context:
                context.close()
        except Exception:
            pass
        
    return best_url

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

def update_userid_file(file_path, user_id, username, timestamp_str):
    """
    更新用户 ID 文本配置文件中的行，回写最近一次成功抓取的时间戳。
    同时自动更新或补全用户昵称字段以保持文档易读性。
    """
    if not file_path or not os.path.exists(file_path):
        return

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated = False
    new_lines = []
    target_id_str = str(user_id).strip()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
            
        parts = stripped.split()
        if parts and parts[0] == target_id_str:
            clean_username = username.replace(" ", "_") if username else "unknown"
            new_line = f"{target_id_str} {clean_username} {timestamp_str}\n"
            new_lines.append(new_line)
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        clean_username = username.replace(" ", "_") if username else "unknown"
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        new_lines.append(f"{target_id_str} {clean_username} {timestamp_str}\n")

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"✅ 已更新 {file_path} 中的用户 {user_id} ({username}) 时间戳为 {timestamp_str}")


def load_existing_post_ids(user_name):
    """
    从 SQLite, CSV 或 Markdown 中加载已经抓取过的微博 ID，用于增量去重判定
    """
    scraped_ids = set()
    user_dir = os.path.join(OUTPUT_DIR, user_name)
    if not os.path.exists(user_dir):
        return scraped_ids

    # 1. 从 SQLite 读取
    db_path = os.path.join(user_dir, "posts.db")
    if ENABLE_SAVE_SQLITE and os.path.exists(db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='posts'")
            if cursor.fetchone():
                cursor.execute("SELECT id FROM posts")
                for row in cursor.fetchall():
                    if row[0]:
                        scraped_ids.add(str(row[0]).strip())
            conn.close()
            print(f"ℹ️ 从 SQLite 加载了 {len(scraped_ids)} 个已抓取的微博 ID")
        except Exception as e:
            print(f"⚠️ 从 SQLite 加载已抓取 ID 失败: {e}")

    # 2. 从 CSV 读取 (作为补充或在 SQLite 禁用时使用)
    csv_path = os.path.join(user_dir, "posts.csv")
    if ENABLE_SAVE_CSV and os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path, dtype={"id": str})
            if "id" in df.columns:
                for val in df["id"].dropna():
                    scraped_ids.add(str(val).strip())
            print(f"ℹ️ 从 CSV 加载后，共有 {len(scraped_ids)} 个已抓取的微博 ID")
        except Exception as e:
            print(f"⚠️ 从 CSV 加载已抓取 ID 失败: {e}")

    # 3. 从 Markdown 文件读取 (如果 SQLite 和 CSV 都没启用，或者作为最后的兜底)
    if not scraped_ids and ENABLE_SAVE_MARKDOWN:
        try:
            for root, dirs, files in os.walk(user_dir):
                for file in files:
                    if file.endswith(".md"):
                        md_path = os.path.join(root, file)
                        posts = parse_markdown_posts(md_path)
                        for p in posts:
                            if p.get("id"):
                                scraped_ids.add(str(p["id"]).strip())
            if scraped_ids:
                print(f"ℹ️ 从 Markdown 文件加载了 {len(scraped_ids)} 个已抓取的微博 ID")
        except Exception as e:
            print(f"⚠️ 从 Markdown 加载已抓取 ID 失败: {e}")

    return scraped_ids


def scrape_weibo_search():
    if not os.path.exists(STATE_FILE):
        print(f"错误: 未找到 {STATE_FILE}。请先运行 login.py 进行登录。")
        return

    user_ids = get_user_ids(TARGET_USER_IDS)
    if not user_ids:
        print("错误: 未配置有效的目标用户 ID。")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        headless_browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        for user_idx, user_info in enumerate(user_ids, 1):
            user_id = user_info["id"]
            print(f"\n==========================================")
            print(f"开始抓取第 {user_idx}/{len(user_ids)} 个用户 ID: {user_id}")
            print(f"==========================================")

            # 自动获取用户昵称
            user_name = fetch_user_name(page, user_id)
            
            # 加载已存在的微博 ID 进行增量去重判定
            scraped_ids = load_existing_post_ids(user_name)
            
            # 记录本次抓取的起始时间点作为下一次增量的起点
            run_start_time = datetime.now().replace(microsecond=0)
            
            # 计算该用户的抓取时间范围
            if user_info["start_time"] is not None:
                # 增量抓取回溯窗口：向前推 INCREMENTAL_LOOKBACK_DAYS 天，防止因新浪微博搜索索引延迟漏掉微博
                user_start_dt = user_info["start_time"] - timedelta(days=INCREMENTAL_LOOKBACK_DAYS)
                user_start_date_str = user_start_dt.strftime("%Y-%m-%d")
            else:
                user_start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
                user_start_date_str = START_DATE
            
            if END_DATE:
                user_end_dt = datetime.strptime(END_DATE, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                user_end_date_str = END_DATE
            else:
                user_end_dt = run_start_time
                user_end_date_str = run_start_time.strftime("%Y-%m-%d")
            
            user_date_ranges = get_date_ranges(user_start_date_str, user_end_date_str, step_days=1)
            print(f"时间段切分为 {len(user_date_ranges)} 个时间范围进行搜索。")
            
            # 每个用户的独立去重与存储
            all_posts = []
            processed_ids = set() # 用于去重

            for start_str, end_str in user_date_ranges:
                print(f"\n=== 用户 {user_name} ({user_id}) | 开始抓取时间段: {start_str} 至 {end_str} ===")
                
                # 结束日期（在 ID 去重模式下，可直接使用 end_str，不再需要增加 1 天）
                search_end_str = end_str
                
                # 构造搜索 URL
                search_url = (
                    f"https://s.weibo.com/weibo?q=uid:{user_id}"
                    f"&typeall=1&suball=1"
                    f"&timescope=custom:{start_str}:{search_end_str}"
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
                    if page.locator("div.card-no-result").is_visible():
                        print("该时间段无数据。")
                        break
                    
                    # 获取卡片
                    cards = page.locator("div[action-type='feed_list_item']").all()
                    if not cards:
                        cards = page.locator("div.card-wrap").all()
                        cards = [c for c in cards if c.locator("p.txt").count() > 0]
                    
                    print(f"当前页发现 {len(cards)} 条微博")
                    
                    for card in cards:
                        try:
                            # 7. 原创微博过滤
                            if ONLY_ORIGINAL == 1:
                                is_retweet = card.locator("div.card-comment").is_visible()
                                if is_retweet:
                                    # print("  -> 跳过转发微博")
                                    continue

                            # 1. 解析时间
                            from_el = card.locator("p.from").first
                            time_str = ""
                            post_link = ""
                            
                            if from_el.is_visible():
                                time_link_el = from_el.locator("a[target='_blank']").first
                                if not time_link_el.is_visible():
                                    time_link_el = from_el.locator("a").first
                                
                                if time_link_el.is_visible():
                                    time_str = time_link_el.inner_text().strip()
                                    post_link = time_link_el.get_attribute("href")
                                else:
                                    print("  -> 提示: p.from 中未找到链接，尝试解析文本")
                                    raw_text = from_el.inner_text()
                                    time_str = raw_text.split("来自")[0].strip()
                            else:
                                card_text = card.inner_text()
                                date_match = re.search(r"(\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{1,2})", card_text)
                                if not date_match:
                                    date_match = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", card_text)
                                if not date_match:
                                    date_match = re.search(r"(今天\s*\d{1,2}:\d{2})", card_text)
                                if not date_match:
                                    date_match = re.search(r"(\d+分钟前)", card_text)
                                if not date_match:
                                    date_match = re.search(r"(\d+小时前)", card_text)
                                if not date_match:
                                    date_match = re.search(r"(刚刚)", card_text)
                                
                                if date_match:
                                    time_str = date_match.group(1)
                                    print(f"  -> 提示: 通过全文正则找到时间: {time_str}")
                                else:
                                    print(f"  -> 跳过: 找不到 p.from 且全文未匹配到日期. 文本片段: {card_text[:50].replace(chr(10), ' ')}...")
                                    continue
                                
                            if not time_str:
                                print("  -> 跳过: 时间文本为空")
                                continue

                            reference_dt = datetime.strptime(start_str, "%Y-%m-%d")
                            post_time = parse_weibo_time(time_str, reference_date=reference_dt)
                            if not post_time:
                                print(f"  -> 跳过: 时间解析失败 '{time_str}'")
                                continue
                                
                            # 2. 提取链接和ID
                            post_id = card.get_attribute("mid")
                            if post_id:
                                post_id = post_id.strip()

                            if not post_link:
                                 if from_el.is_visible():
                                     link_el = from_el.locator("a").first
                                     if link_el.is_visible():
                                         post_link = link_el.get_attribute("href")

                            if post_link and post_link.startswith("//"):
                                post_link = "https:" + post_link
                            
                            if not post_link and post_id and post_id.isdigit():
                                post_link = f"https://weibo.com/{user_id}/{post_id}"
                            
                            if not post_id:
                                if post_link:
                                    post_id = post_link.split("/")[-1].split("?")[0]
                                else:
                                    post_id = str(post_time)

                            # --- 去重与时间过滤 ---
                            if post_id in processed_ids:
                                print(f"  -> 跳过本次已处理的重复微博: {post_id}")
                                continue
                                
                            if post_id in scraped_ids:
                                print(f"  -> 跳过历史已抓取的微博: {post_id}")
                                continue
                                
                            # 严格时间范围过滤
                            if user_info["start_time"] is not None:
                                # 对于增量用户，如果该微博不在已抓取列表中，只要在回溯范围之内，我们都予以抓取以防漏掉
                                if post_time < user_start_dt:
                                    print(f"  -> 跳过早于回溯起点 ({user_start_dt}) 的微博: {post_time}")
                                    continue
                                if post_time > user_end_dt:
                                    print(f"  -> 跳过晚于本次抓取终点 ({user_end_dt}) 的微博: {post_time}")
                                    continue
                            else:
                                if not (user_start_dt <= post_time <= user_end_dt):
                                    print(f"  -> 跳过不在时间范围内的微博: {post_time}")
                                    continue
                            # ----------------
                            
                            processed_ids.add(post_id)

                            # 3. 展开全文
                            expand_btn = card.locator("a[action-type='fl_unfold']").first
                            if expand_btn.is_visible():
                                print("点击展开全文...")
                                try:
                                    expand_btn.evaluate("el => el.click()")
                                    page.wait_for_timeout(500)
                                except:
                                    pass
                            
                            # 4. 提取正文
                            content_full = card.locator("p[node-type='feed_list_content_full']").first
                            content_normal = card.locator("p.txt").first
                            
                            if content_full.is_visible():
                                content = content_full.inner_text()
                            else:
                                content = content_normal.inner_text()
                                
                            # 5. 提取互动数据
                            footer = card.locator("div.card-act").first
                            stats_text = footer.inner_text().replace("\n", " ").strip() if footer.is_visible() else ""
                            
                            # 5.5 提取图片链接
                            images = []
                            if ENABLE_SAVE_IMAGES:
                                try:
                                    img_locators = card.locator("div.media-piclist img").all()
                                    for img_loc in img_locators:
                                        src = img_loc.get_attribute("src")
                                        if src:
                                            if src.startswith("//"):
                                                src = "https:" + src
                                            large_src = re.sub(r'/(thumb150|orj360|mw690|orj960|small|thumbnail)/', '/large/', src)
                                            images.append(large_src)
                                except Exception as img_err:
                                    print(f"提取图片链接失败: {img_err}")
                            
                            # 5.6 提取视频链接
                            videos = []
                            download_videos = []
                            if ENABLE_SAVE_VIDEOS:
                                try:
                                    video_el = card.locator("a.WB_video_h5").first
                                    video_url = ""
                                    detail_url = ""
                                    
                                    if video_el.is_visible():
                                        data_str = video_el.get_attribute("data-str")
                                        if data_str:
                                            address_match = re.search(r"address\s*:\s*['\"]([^'\"]+)['\"]", data_str)
                                            if address_match:
                                                detail_url = address_match.group(1)
                                                if detail_url.startswith("//"):
                                                    detail_url = "https:" + detail_url
                                                video_url = extract_high_quality_video(detail_url, headless_browser)
                                                
                                    if not video_url and video_el.is_visible():
                                        data_str = video_el.get_attribute("data-str")
                                        if data_str:
                                            video_match = re.search(r"src\s*:\s*['\"](//[^'\"]+\.mp4[^'\"]*)['\"]", data_str)
                                            if video_match:
                                                video_url = video_match.group(1)
                                                
                                    if not video_url:
                                        tech_video = card.locator("video.wbpv-tech").first
                                        if tech_video.is_visible():
                                            video_url = tech_video.get_attribute("src")
                                            
                                    if video_url:
                                        if video_url.startswith("//"):
                                            video_url = "https:" + video_url
                                        import html
                                        video_url = html.unescape(video_url)
                                        
                                        stable_url = detail_url if detail_url else video_url
                                        videos.append(stable_url)
                                        download_videos.append(video_url)
                                except Exception as video_err:
                                    print(f"提取视频链接失败: {video_err}")

                            # 5.7 提取 Live Photo 的视频链接
                            livephotos = []
                            if ENABLE_SAVE_LIVEPHOTOS and images:
                                try:
                                    api_url = f"https://weibo.com/ajax/statuses/show?id={post_id}"
                                    api_res = page.context.request.get(api_url)
                                    if api_res.status == 200:
                                        post_detail = api_res.json()
                                        pic_infos = post_detail.get("pic_infos", {})
                                        for pic_id, pic_data in pic_infos.items():
                                            if pic_data.get("type") == "livephoto" and pic_data.get("video"):
                                                livephotos.append(pic_data.get("video"))
                                except Exception as lp_err:
                                    print(f"提取实况照片视频失败 (ID: {post_id}): {lp_err}")
                            
                            reposts_c, comments_c, attitudes_c = parse_weibo_stats(stats_text)
                            post_data = {
                                "id": post_id,
                                "time": post_time,
                                "link": post_link,
                                "content": content,
                                "reposts_count": reposts_c,
                                "comments_count": comments_c,
                                "attitudes_count": attitudes_c,
                                "stats_raw": stats_text,
                                "images": images,
                                "videos": videos,
                                "download_videos": download_videos,
                                "livephotos": livephotos
                            }
                            all_posts.append(post_data)
                            print(f"抓取: {post_time} - {content[:10]}...")
                            
                        except Exception as e:
                            print(f"解析出错: {e}")
                            continue
                    
                    # 寻找下一页
                    next_btn = page.locator("a.next").first
                    if next_btn.is_visible():
                        print("点击下一页...")
                        try:
                            next_btn.click()
                            time.sleep(2)
                        except Exception as e:
                            print(f"翻页失败: {e}")
                            break
                    else:
                        print("已到达最后一页。")
                        break
                
                # 每次月/时间段抓完保存一次，防数据丢失
                save_data(all_posts, user_name, page)
                
                time.sleep(3)

            # 该用户完全抓取成功后，更新对应文件的增量时间戳
            if TARGET_USER_IDS.endswith(".txt"):
                run_start_time_str = run_start_time.strftime("%Y-%m-%dT%H:%M:%S")
                update_userid_file(TARGET_USER_IDS, user_id, user_name, run_start_time_str)

        try:
            headless_browser.close()
        except:
            pass


def parse_markdown_posts(file_path):
    """
    解析已存在的 Markdown 存档文件，提取出其中的微博列表。
    返回格式: [{"id": "...", "time_str": "HH:MM:SS", "body": "..."}]
    """
    if not os.path.exists(file_path):
        return []
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"⚠️ 读取 Markdown 文件失败: {e}")
        return []

    # 按 "\n## " 分割各条微博
    parts = content.split("\n## ")
    if len(parts) <= 1:
        parts = content.split("## ")
        if len(parts) <= 1:
            return []

    posts = []
    start_idx = 1
    if parts[0].startswith("## "):
        start_idx = 0
        parts[0] = parts[0][3:]

    for part in parts[start_idx:]:
        lines = part.split("\n")
        if not lines:
            continue
        time_display = lines[0].strip()
        if not time_display:
            continue
        
        post_id = ""
        id_match = re.search(r"微博ID:\s*`(\d+)`", part)
        if id_match:
            post_id = id_match.group(1)
            
        cleaned_body = part.strip()
        body_lines = cleaned_body.split("\n")[1:]
        body_content = "\n".join(body_lines).strip()
        if body_content.endswith("---"):
            body_content = body_content[:-3].strip()
        elif "\n---" in body_content:
            body_content = body_content.rsplit("\n---", 1)[0].strip()
            
        posts.append({
            "id": post_id,
            "time_str": time_display,
            "body": body_content
        })
        
    return posts


def save_to_csv(data, user_name):
    """
    将用户的抓取结果保存到 weibo/用户名/posts.csv 文本文件中。
    使用增量更新机制：如果文件已存在，先读取旧数据进行合并去重后再写入。
    """
    if not data:
        return
    csv_dir = os.path.join(OUTPUT_DIR, user_name)
    os.makedirs(csv_dir, exist_ok=True)
    csv_path = os.path.join(csv_dir, "posts.csv")
    
    df_data = []
    for post in data:
        df_data.append({
            "id": str(post.get("id")),
            "time": post.get("time").strftime("%Y-%m-%d %H:%M:%S") if isinstance(post.get("time"), datetime) else str(post.get("time")),
            "link": post.get("link"),
            "content": post.get("content"),
            "reposts_count": post.get("reposts_count", 0),
            "comments_count": post.get("comments_count", 0),
            "attitudes_count": post.get("attitudes_count", 0),
            "images": ",".join(post.get("images", [])),
            "videos": ",".join(post.get("videos", [])),
            "livephotos": ",".join(post.get("livephotos", []))
        })
        
    df_new = pd.DataFrame(df_data)
    
    if os.path.exists(csv_path):
        try:
            df_old = pd.read_csv(csv_path, dtype={"id": str})
            df_old["id"] = df_old["id"].astype(str)
            df_combined = pd.concat([df_new, df_old]).drop_duplicates(subset=["id"], keep="first")
            df_combined = df_combined.sort_values(by="time", ascending=True)
            df_combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
            print(f"✅ 已增量更新 CSV 数据文件: {csv_path}")
            return
        except Exception as e:
            print(f"⚠️ 读取/合并旧 CSV 失败: {e}，将直接重写。")
            
    df_new.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"✅ 已导出 CSV 数据文件: {csv_path}")


def save_to_sqlite(data, user_name):
    """
    将用户的抓取结果写入 weibo/用户名/posts.db 本地 SQLite 数据库中。
    """
    if not data:
        return
    import sqlite3
    import json
    
    db_dir = os.path.join(OUTPUT_DIR, user_name)
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "posts.db")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            time TEXT,
            link TEXT,
            content TEXT,
            reposts_count INTEGER,
            comments_count INTEGER,
            attitudes_count INTEGER,
            images TEXT,
            videos TEXT,
            livephotos TEXT
        )
    """)
    conn.commit()
    
    for post in data:
        cursor.execute("""
            INSERT OR REPLACE INTO posts (id, time, link, content, reposts_count, comments_count, attitudes_count, images, videos, livephotos)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post.get("id"),
            post.get("time").strftime("%Y-%m-%d %H:%M:%S") if isinstance(post.get("time"), datetime) else str(post.get("time")),
            post.get("link"),
            post.get("content"),
            post.get("reposts_count", 0),
            post.get("comments_count", 0),
            post.get("attitudes_count", 0),
            json.dumps(post.get("images", [])),
            json.dumps(post.get("videos", [])),
            json.dumps(post.get("livephotos", []))
        ))
        
    conn.commit()
    conn.close()
    print(f"✅ 已同步 SQLite 数据库: {db_path}")


def save_data(data, user_name, page=None):
    # 按日期分组
    posts_by_date = {}
    for post in data:
        date_str = post['time'].strftime('%Y-%m-%d')
        if date_str not in posts_by_date:
            posts_by_date[date_str] = []
        posts_by_date[date_str].append(post)
    
    for date_str, new_posts in posts_by_date.items():
        # 构建目录结构: OUTPUT_DIR/用户名/YYYY-MM/
        month_str = date_str[:7]  # "YYYY-MM"
        month_dir = os.path.join(OUTPUT_DIR, user_name, month_str)
        img_dir = os.path.join(month_dir, "img")
        video_dir = os.path.join(month_dir, "video")
        livephoto_dir = os.path.join(month_dir, "livephoto")
        os.makedirs(month_dir, exist_ok=True)
        if ENABLE_SAVE_IMAGES:
            os.makedirs(img_dir, exist_ok=True)
        
        file_name = f"{date_str}.md"
        file_path = os.path.join(month_dir, file_name)
        
        # 1. 组装新爬取微博的 Markdown 部分并下载对应的多媒体文件
        new_md_posts = []
        for post in new_posts:
            time_display = post['time'].strftime('%H:%M:%S')
            post_id = post.get("id", str(post['time']))
            
            body_lines = []
            # 写入微博链接与ID
            link_part = f"[{post['link']}]({post['link']})" if post.get('link') else ""
            id_part = f"微博ID: `{post['id']}`" if (post.get('id') and post['id'] != str(post['time'])) else ""
            
            if link_part and id_part:
                body_lines.append(f"**微博链接:** {link_part} | {id_part}\n")
            elif link_part:
                body_lines.append(f"**微博链接:** {link_part}\n")
            elif id_part:
                body_lines.append(f"**{id_part}**\n")
            
            reposts = post.get("reposts_count", 0)
            comments = post.get("comments_count", 0)
            likes = post.get("attitudes_count", 0)
            body_lines.append(f"**互动数据:** 转发 {reposts} | 评论 {comments} | 点赞 {likes}\n")
            body_lines.append(f"{post['content']}\n")
            
            # 保存图片逻辑
            if ENABLE_SAVE_IMAGES and post.get("images"):
                local_image_paths = []
                safe_post_id = re.sub(r'[^\w\-]', '_', post_id)
                time_prefix = post['time'].strftime('%Y%m%d_%H%M%S')
                
                for idx, img_url in enumerate(post["images"], start=1):
                    ext = "jpg"
                    ext_match = re.search(r'\.(\w+)(?:\?|$)', img_url)
                    if ext_match:
                        ext = ext_match.group(1)
                    
                    img_name = f"{time_prefix}_{safe_post_id}_{idx}.{ext}"
                    img_save_path = os.path.join(img_dir, img_name)
                    
                    if not os.path.exists(img_save_path):
                        print(f"正在下载图片 {idx}/{len(post['images'])}: {img_url}")
                        success = download_file(img_url, img_save_path, page)
                        if success:
                            local_image_paths.append(f"./img/{img_name}")
                    else:
                        local_image_paths.append(f"./img/{img_name}")
                        
                if local_image_paths:
                    body_lines.append("")
                    for local_path in local_image_paths:
                        body_lines.append(f"![微博图片]({local_path})")
                    body_lines.append("")
                        
            # 保存视频逻辑
            # 优先使用 download_videos (CDN 直链) 进行本地下载，如果不存在则使用 videos (主要针对历史记录回显)
            video_urls_to_download = post.get("download_videos") if post.get("download_videos") is not None else post.get("videos", [])
            if ENABLE_SAVE_VIDEOS and video_urls_to_download:
                os.makedirs(video_dir, exist_ok=True)
                local_video_paths = []
                safe_post_id = re.sub(r'[^\w\-]', '_', post_id)
                time_prefix = post['time'].strftime('%Y%m%d_%H%M%S')
                
                for idx, video_url in enumerate(video_urls_to_download, start=1):
                    # 如果是网页链接，我们直接跳过下载 (它们是保存在 CSV/SQLite 中的稳定地址)
                    if "video.weibo.com" in video_url or "weibo.com/tv" in video_url:
                        continue
                    ext = "mp4"
                    ext_match = re.search(r'\.(\w+)(?:\?|$)', video_url)
                    if ext_match:
                        ext = ext_match.group(1)
                    
                    video_name = f"{time_prefix}_{safe_post_id}_{idx}.{ext}"
                    video_save_path = os.path.join(video_dir, video_name)
                    
                    if not os.path.exists(video_save_path):
                        print(f"正在下载视频 {idx}/{len(video_urls_to_download)}: {video_url}")
                        success = download_file(video_url, video_save_path, page)
                        if success:
                            local_video_paths.append(f"./video/{video_name}")
                    else:
                        local_video_paths.append(f"./video/{video_name}")
                        
                if local_video_paths:
                    body_lines.append("")
                    for local_path in local_video_paths:
                        body_lines.append(f'<video src="{local_path}" controls width="100%"></video>')
                    body_lines.append("")
                        
            # 保存实况照片 (Live Photo) 逻辑
            if ENABLE_SAVE_LIVEPHOTOS and post.get("livephotos"):
                os.makedirs(livephoto_dir, exist_ok=True)
                local_livephoto_paths = []
                safe_post_id = re.sub(r'[^\w\-]', '_', post_id)
                time_prefix = post['time'].strftime('%Y%m%d_%H%M%S')
                
                for idx, lp_url in enumerate(post["livephotos"], start=1):
                    ext = "mov"
                    ext_match = re.search(r'\.(\w+)(?:\?|$)', lp_url)
                    if ext_match:
                        ext = ext_match.group(1)
                    
                    lp_name = f"{time_prefix}_{safe_post_id}_{idx}.{ext}"
                    lp_save_path = os.path.join(livephoto_dir, lp_name)
                    
                    if not os.path.exists(lp_save_path):
                        print(f"正在下载实况视频 {idx}/{len(post['livephotos'])}: {lp_url}")
                        success = download_file(lp_url, lp_save_path, page)
                        if success:
                            local_livephoto_paths.append(f"./livephoto/{lp_name}")
                    else:
                        local_livephoto_paths.append(f"./livephoto/{lp_name}")
                        
                if local_livephoto_paths:
                    body_lines.append("\n*实况照片动效视频:*")
                    for local_path in local_livephoto_paths:
                        body_lines.append(f'<video src="{local_path}" controls width="100%"></video>')
                    body_lines.append("")

            if ENABLE_SAVE_MARKDOWN:
                new_md_posts.append({
                    "id": post_id,
                    "time_str": time_display,
                    "body": "\n".join(body_lines).strip()
                })

        if ENABLE_SAVE_MARKDOWN:
            # 2. 读取并解析旧微博归档
            old_md_posts = parse_markdown_posts(file_path)
            
            # 3. 合并及去重
            combined_posts = new_md_posts + old_md_posts
            seen_ids = set()
            seen_keys = set()
            merged_posts = []
            for p in combined_posts:
                pid = p.get("id")
                time_str = p.get("time_str")
                body_content = p.get("body")
                
                if pid and pid != time_str:
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                else:
                    key = (time_str, hash(body_content))
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                merged_posts.append(p)
                
            # 4. 排序：按时间正序排列
            merged_posts.sort(key=lambda x: x['time_str'])
            
            # 5. 写回文件
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"# {date_str} 微博存档\n\n")
                    for p in merged_posts:
                        f.write(f"## {p['time_str']}\n\n")
                        f.write(f"{p['body']}\n\n")
                        f.write(f"---\n\n")
            except Exception as e:
                print(f"保存文件 {file_name} 失败: {e}")
            
    user_dir = os.path.join(OUTPUT_DIR, user_name)
    print(f"当前已保存 {len(data)} 条数据到 {user_dir}")
    
    # 额外存储为 CSV 和 SQLite 数据库
    if ENABLE_SAVE_CSV:
        try:
            save_to_csv(data, user_name)
        except Exception as csv_err:
            print(f"保存 CSV 失败: {csv_err}")
            
    if ENABLE_SAVE_SQLITE:
        try:
            save_to_sqlite(data, user_name)
        except Exception as sqlite_err:
            print(f"保存 SQLite 失败: {sqlite_err}")
            

if __name__ == "__main__":
    scrape_weibo_search()
