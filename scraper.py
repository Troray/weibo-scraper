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

# 原创/转发图片视频实况下载设置映射到底层参数
ORIGINAL_PIC_DOWNLOAD = getattr(config, "ORIGINAL_PIC_DOWNLOAD", 1)
RETWEET_PIC_DOWNLOAD = getattr(config, "RETWEET_PIC_DOWNLOAD", 1)
ORIGINAL_VIDEO_DOWNLOAD = getattr(config, "ORIGINAL_VIDEO_DOWNLOAD", 1)
RETWEET_VIDEO_DOWNLOAD = getattr(config, "RETWEET_VIDEO_DOWNLOAD", 0)
ORIGINAL_LIVE_PHOTO_DOWNLOAD = getattr(config, "ORIGINAL_LIVE_PHOTO_DOWNLOAD", 1)
RETWEET_LIVE_PHOTO_DOWNLOAD = getattr(config, "RETWEET_LIVE_PHOTO_DOWNLOAD", 0)

ENABLE_SAVE_IMAGES = (ORIGINAL_PIC_DOWNLOAD or RETWEET_PIC_DOWNLOAD)
ENABLE_SAVE_VIDEOS = (ORIGINAL_VIDEO_DOWNLOAD or RETWEET_VIDEO_DOWNLOAD)
ENABLE_SAVE_LIVEPHOTOS = (ORIGINAL_LIVE_PHOTO_DOWNLOAD or RETWEET_LIVE_PHOTO_DOWNLOAD)

ENABLE_SAVE_CSV = config.ENABLE_SAVE_CSV
ENABLE_SAVE_SQLITE = config.ENABLE_SAVE_SQLITE
ENABLE_SAVE_MARKDOWN = config.ENABLE_SAVE_MARKDOWN
ENABLE_SAVE_JSON = getattr(config, "ENABLE_SAVE_JSON", 0)
ENABLE_SCRAPE_COMMENTS = getattr(config, "ENABLE_SCRAPE_COMMENTS", 0)
MAX_COMMENTS_PER_POST = getattr(config, "MAX_COMMENTS_PER_POST", 100)
MAX_REPLIES_PER_COMMENT = getattr(config, "MAX_REPLIES_PER_COMMENT", 50)
DOWNLOAD_MIN_MULTIPART_SIZE_MB = getattr(config, "DOWNLOAD_MIN_MULTIPART_SIZE_MB", 15)
DOWNLOAD_NUM_THREADS = getattr(config, "DOWNLOAD_NUM_THREADS", 5)
ONLY_ORIGINAL = config.ONLY_ORIGINAL
ENABLE_SAVE_COMMENT_MEDIA = getattr(config, "ENABLE_SAVE_COMMENT_MEDIA", 0)
SAVE_DATA_BY_PERIOD = getattr(config, "SAVE_DATA_BY_PERIOD", "both")
DOWNLOAD_NUM_CONCURRENT_MEDIA = getattr(config, "DOWNLOAD_NUM_CONCURRENT_MEDIA", 5)
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
            parsed_dt = None
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed_dt = datetime.strptime(part, fmt)
                    break
                except ValueError:
                    continue
            if parsed_dt is not None:
                start_time = parsed_dt
            else:
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


def download_file(url, save_path, page=None, show_progress=True):
    """
    流式分块下载函数，支持多线程下载、实时进度显示、网速监控和超时处理。
    """
    import urllib.request
    import threading
    import sys
    import time
    
    referer = "https://weibo.com/"
    if "video.weibo.com" in url or "show.weibo.com" in url or "weibo.com/tv" in url:
        referer = "https://video.weibo.com/"
    headers = {
        "Referer": referer,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    def print_progress(downloaded, total, speed=0.0):
        if not show_progress:
            return
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

    # 根据配置的大于指定大小且支持 Range 请求时使用多线程下载
    MIN_MULTIPART_SIZE = DOWNLOAD_MIN_MULTIPART_SIZE_MB * 1024 * 1024
    NUM_THREADS = DOWNLOAD_NUM_THREADS
    
    if support_ranges and total_size > MIN_MULTIPART_SIZE:
        if show_progress:
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
                if show_progress:
                    print("\n✅ 多线程下载完成。")
                return True
            else:
                if show_progress:
                    print(f"\n⚠️ 多线程下载失败，已降级为单线程流式下载。错误: {errors}")
                
    # 2. 降级为单线程流式下载
    if show_progress:
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
            if show_progress:
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

def extract_comment_media_url(item):
    """
    从评论或回复的 JSON 数据中提取图片/动图 URL。
    """
    if not item:
        return ""
        
    img_url = ""
    # 1. 尝试从 url_struct (通常是评论图片存放的位置) 提取
    url_struct = item.get("url_struct")
    if url_struct and isinstance(url_struct, list):
        for url_obj in url_struct:
            if isinstance(url_obj, dict):
                pic_infos = url_obj.get("pic_infos")
                if pic_infos and isinstance(pic_infos, dict):
                    for pic_id, pic_data in pic_infos.items():
                        if isinstance(pic_data, dict):
                            for sz in ["woriginal", "large", "bmiddle", "thumbnail"]:
                                if sz in pic_data and pic_data[sz].get("url"):
                                    img_url = pic_data[sz]["url"]
                                    break
                        if img_url:
                            break
            if img_url:
                break

    # 2. 尝试从根级 pic_infos 获取
    if not img_url:
        pic_infos = item.get("pic_infos")
        if pic_infos and isinstance(pic_infos, dict):
            for pic_id, pic_data in pic_infos.items():
                for sz in ["original", "woriginal", "large", "bmiddle", "thumbnail"]:
                    if sz in pic_data and pic_data[sz].get("url"):
                        img_url = pic_data[sz]["url"]
                        break
                if img_url:
                    break
                    
    # 3. 尝试从根级 pic 获取
    if not img_url:
        pic_data = item.get("pic")
        if pic_data:
            if isinstance(pic_data, dict):
                for sz in ["original", "woriginal", "large", "bmiddle", "thumbnail", "url"]:
                    if sz in pic_data:
                        val = pic_data[sz]
                        if isinstance(val, dict) and val.get("url"):
                            img_url = val["url"]
                            break
                        elif isinstance(val, str) and val.startswith("http"):
                            img_url = val
                            break
            elif isinstance(pic_data, str) and pic_data.startswith("http"):
                img_url = pic_data
                
    return img_url

def scrape_replies(page, post_id, comment_id, post_author_uid=""):
    """
    通过微博 AJAX 接口爬取某条主评论下的楼中楼回复。
    返回子评论列表，格式: [{"id": "...", "post_id": "...", "parent_id": "...", "time": "...", "user_id": "...", "user_name": "...", "content": "...", "like_count": 0, "media_url": "..."}]
    """
    replies = []
    max_id = 0
    url_template = "https://weibo.com/ajax/statuses/buildComments?is_reload=1&id={comment_id}&is_show_bulletin=2&is_mix=1&fetch_level=1&count=20&uid={uid}"
    
    headers = {
        "Referer": f"https://weibo.com/detail/{post_id}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # 限制每个主评论最多爬取的子回复数，以防请求过多被封
    max_replies_limit = MAX_REPLIES_PER_COMMENT
    
    while len(replies) < max_replies_limit:
        url = url_template.format(comment_id=comment_id, uid=post_author_uid)
        if max_id > 0:
            url += f"&max_id={max_id}"
            
        try:
            response = page.context.request.get(url, headers=headers)
            if response.status != 200:
                break
                
            res_json = response.json()
            if not res_json or res_json.get("ok") != 1:
                break
                
            data = res_json.get("data", [])
            if not data:
                break
                
            for item in data:
                if len(replies) >= max_replies_limit:
                    break
                    
                reply_id = str(item.get("id"))
                reply_text_raw = item.get("text", "")
                reply_text = re.sub(r'<[^>]+>', '', reply_text_raw).strip()
                
                user_info = item.get("user", {})
                user_id = str(user_info.get("id", ""))
                user_name = user_info.get("screen_name", "未知用户")
                
                created_at_str = item.get("created_at", "")
                reply_time = ""
                if created_at_str:
                    try:
                        dt = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
                        reply_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        reply_time = str(created_at_str)
                        
                like_count = item.get("like_counts", 0)
                media_url = extract_comment_media_url(item)
                
                replies.append({
                    "id": reply_id,
                    "post_id": post_id,
                    "parent_id": comment_id,
                    "time": reply_time,
                    "user_id": user_id,
                    "user_name": user_name,
                    "content": reply_text,
                    "like_count": like_count,
                    "media_url": media_url,
                    "source": item.get("source", "")
                })
                
            max_id = res_json.get("max_id", 0)
            if max_id == 0:
                break
            # 适当延时防反爬
            time.sleep(0.5)
            
        except Exception as e:
            print(f"      ⚠️ 爬取楼中楼出错 (ID: {comment_id}): {e}")
            break
            
    return replies

def scrape_comments(page, post_id, target_user_id=""):
    """
    通过微博 AJAX 接口爬取一条微博的评论，并包含子评论（楼中楼）。
    返回主评论列表，格式: [{"id": "...", "post_id": "...", "time": "...", "user_id": "...", "user_name": "...", "content": "...", "like_count": 0, "media_url": "...", "replies": [...]}]
    """
    if not post_id:
        return []
        
    comments = []
    max_id = 0
    url_template = "https://weibo.com/ajax/statuses/buildComments?is_show_bulletin=2&id={post_id}&is_mix=0&count=20&uid=&fetch_level=0"
    
    print(f"  -> 开始爬取微博 {post_id} 的评论区...")
    
    headers = {
        "Referer": f"https://weibo.com/detail/{post_id}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    while len(comments) < MAX_COMMENTS_PER_POST:
        url = url_template.format(post_id=post_id)
        if max_id > 0:
            url += f"&max_id={max_id}"
            
        try:
            # 使用 Playwright 的 context.request 发送带有当前登录状态 (Cookies) 的 GET 请求
            response = page.context.request.get(url, headers=headers)
            if response.status != 200:
                print(f"    ⚠️ 获取评论接口返回状态码: {response.status}")
                break
                
            res_json = response.json()
            if not res_json or res_json.get("ok") != 1:
                break
                
            data = res_json.get("data", [])
            if not data:
                break
                
            for item in data:
                if len(comments) >= MAX_COMMENTS_PER_POST:
                    break
                
                comment_id = str(item.get("id"))
                comment_text_raw = item.get("text", "")
                
                # 去除 HTML 标签，如超链接或表情图片标签
                comment_text = re.sub(r'<[^>]+>', '', comment_text_raw).strip()
                
                user_info = item.get("user", {})
                user_id = str(user_info.get("id", ""))
                user_name = user_info.get("screen_name", "未知用户")
                
                # 评论时间处理
                created_at_str = item.get("created_at", "")
                comment_time = ""
                if created_at_str:
                    try:
                        # 微博的格式类似 "Mon Jun 01 15:00:00 +0800 2026"
                        dt = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
                        # 转换成无时区的本地时间字符串（方便统一处理）
                        comment_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        comment_time = str(created_at_str)
                
                like_count = item.get("like_counts", 0)
                
                # 处理楼中楼回复
                replies = []
                raw_replies = item.get("comments", [])
                total_replies_cnt = item.get("total_number", 0)
                
                if total_replies_cnt > len(raw_replies):
                    # 如果子评论数量多于默认带回的数据，通过接口拉取完整数据
                    replies = scrape_replies(page, post_id, comment_id, target_user_id)
                else:
                    # 否则，直接解析当前随附的子评论数据以节省请求
                    for r_item in raw_replies:
                        r_id = str(r_item.get("id"))
                        r_text_raw = r_item.get("text", "")
                        r_text = re.sub(r'<[^>]+>', '', r_text_raw).strip()
                        
                        r_user_info = r_item.get("user", {})
                        r_user_id = str(r_user_info.get("id", ""))
                        r_user_name = r_user_info.get("screen_name", "未知用户")
                        
                        r_created_at = r_item.get("created_at", "")
                        r_time = ""
                        if r_created_at:
                            try:
                                r_dt = datetime.strptime(r_created_at, "%a %b %d %H:%M:%S %z %Y")
                                r_time = r_dt.strftime("%Y-%m-%d %H:%M:%S")
                            except Exception:
                                r_time = str(r_created_at)
                        
                        r_like_count = r_item.get("like_counts", 0)
                        r_media_url = extract_comment_media_url(r_item)
                        replies.append({
                            "id": r_id,
                            "post_id": post_id,
                            "parent_id": comment_id,
                            "time": r_time,
                            "user_id": r_user_id,
                            "user_name": r_user_name,
                            "content": r_text,
                            "like_count": r_like_count,
                            "media_url": r_media_url,
                            "source": r_item.get("source", "")
                        })
                
                media_url = extract_comment_media_url(item)
                comments.append({
                    "id": comment_id,
                    "post_id": post_id,
                    "time": comment_time,
                    "user_id": user_id,
                    "user_name": user_name,
                    "content": comment_text,
                    "like_count": like_count,
                    "media_url": media_url,
                    "replies": replies,
                    "source": item.get("source", "")
                })
            
            # 判断是否有下一页
            max_id = res_json.get("max_id", 0)
            if max_id == 0:
                break
                
            # 适当延时防反爬
            time.sleep(1.0)
            
        except Exception as e:
            print(f"    ❌ 爬取评论出错: {e}")
            break
            
    total_count = len(comments) + sum(len(c.get("replies", [])) for c in comments)
    print(f"  -> 微博 {post_id} 成功爬取 {total_count} 条评论 (主评论 {len(comments)} 条，子回复 {total_count - len(comments)} 条)。")
    return comments

def fetch_user_name(page, user_id):
    """
    访问用户微博主页，自动获取用户昵称。
    如果获取失败，回退使用用户 ID。
    """
    profile_url = f"https://weibo.com/u/{user_id}"
    print(f"\n正在获取用户昵称: {profile_url}")
    try:
        page.goto(profile_url)
        page.wait_for_load_state("domcontentloaded", timeout=20000)
        # 给页面一定的加载和网络请求时间
        time.sleep(3)
        
        # 优先通过 API 接口获取昵称
        try:
            info_url = f"https://weibo.com/ajax/profile/info?uid={user_id}"
            info_res = page.evaluate("async (url) => { const r = await fetch(url); return await r.json(); }", info_url)
            if info_res.get("ok") == 1:
                screen_name = info_res.get("data", {}).get("user", {}).get("screen_name")
                if screen_name:
                    print(f"✅ 通过 API 获取到用户昵称: {screen_name}")
                    return screen_name
        except Exception as api_err:
            print(f"⚠️ 通过 API 获取昵称失败: {api_err}，尝试解析网页元素...")

        # 兜底从页面元素获取昵称 (class 名含哈希，使用前缀匹配)
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


def scrape_and_save_user_profile(page, user_id, user_name):
    """
    获取用户的详细个人档案信息，并写入 weibo/用户名/用户id.txt 文件中。
    """
    user_dir = os.path.join(OUTPUT_DIR, user_name)
    os.makedirs(user_dir, exist_ok=True)
    file_path = os.path.join(user_dir, f"{user_id}.txt")
    
    print(f"正在获取用户 {user_name} ({user_id}) 的详细个人信息...")
    
    try:
        # 访问用户主页以确保 Cookies 初始化和请求在上下文内进行
        profile_url = f"https://weibo.com/u/{user_id}"
        page.goto(profile_url)
        page.wait_for_load_state("domcontentloaded", timeout=20000)
        time.sleep(3) # 给页面一定的加载 and 网络请求时间
        
        # 1. 爬取基础资料 info
        info_url = f"https://weibo.com/ajax/profile/info?uid={user_id}"
        info_res = page.evaluate("async (url) => { const r = await fetch(url); return await r.json(); }", info_url)
        user_info = info_res.get("data", {}).get("user", {}) if info_res.get("ok") == 1 else {}
        
        # 2. 爬取详细资料 detail
        detail_url = f"https://weibo.com/ajax/profile/detail?uid={user_id}"
        detail_res = page.evaluate("async (url) => { const r = await fetch(url); return await r.json(); }", detail_url)
        detail_info = detail_res.get("data", {}) if detail_res.get("ok") == 1 else {}
        
        if not user_info and not detail_info:
            print(f"⚠️ 无法通过 API 接口获取用户 {user_id} 的资料")
            return False
            
        # 性别映射
        gender_raw = user_info.get("gender", "")
        gender_map = {"f": "女", "m": "男"}
        gender_str = gender_map.get(gender_raw, gender_raw)
        
        # 教育经历解析
        edu_str = ""
        edu = detail_info.get("education")
        if edu:
            if isinstance(edu, dict):
                school = edu.get("school", "")
                time_val = edu.get("time", "")
                edu_str = f"{school} ({time_val})" if time_val else school
            elif isinstance(edu, list):
                edu_parts = []
                for item in edu:
                    if isinstance(item, dict):
                        school = item.get("school", "")
                        time_val = item.get("time", "")
                        edu_parts.append(f"{school} ({time_val})" if time_val else school)
                    elif isinstance(item, str):
                        edu_parts.append(item)
                edu_str = "、".join(edu_parts)
            elif isinstance(edu, str):
                edu_str = edu
                
        # 工作经历解析
        career_str = ""
        career = detail_info.get("career")
        if career:
            if isinstance(career, dict):
                company = career.get("company", "")
                time_val = career.get("time", "")
                career_str = f"{company} ({time_val})" if time_val else company
            elif isinstance(career, list):
                career_parts = []
                for item in career:
                    if isinstance(item, dict):
                        company = item.get("company", "")
                        time_val = item.get("time", "")
                        career_parts.append(f"{company} ({time_val})" if time_val else company)
                    elif isinstance(item, str):
                        career_parts.append(item)
                career_str = "、".join(career_parts)
            elif isinstance(career, str):
                career_str = career
                
        # 会员等级
        mbrank_val = user_info.get("mbrank")
        mbrank_str = str(mbrank_val) if mbrank_val is not None else "0"
        
        # 微博等级
        urank_val = user_info.get("urank") or detail_info.get("urank")
        urank_str = str(urank_val) if urank_val is not None else ""
        
        # 是否认证
        verified = user_info.get("verified", False)
        verified_str = "True" if verified else "False"
        
        # 认证类型和认证信息
        verified_type_val = user_info.get("verified_type", -1)
        if not verified:
            verified_type_str = "未认证"
        elif verified_type_val == 0:
            verified_type_str = "个人认证"
        elif verified_type_val in (1, 2, 3, 4, 5, 6, 7):
            verified_type_str = "官方认证/机构认证"
        else:
            verified_type_str = "其他认证"
            
        verified_reason = user_info.get("verified_reason", "")
        
        # 简介
        desc = user_info.get("description", "")
        
        # 阳光信用
        sunshine = detail_info.get("sunshine_credit", {}).get("level", "")
        
        # 获取并清洗头像 URLs
        avatar_clean = user_info.get('profile_image_url', '')
        if '?' in avatar_clean:
            avatar_clean = avatar_clean.split('?')[0]
        avatar_hd_clean = user_info.get('avatar_hd', '')
        if '?' in avatar_hd_clean:
            avatar_hd_clean = avatar_hd_clean.split('?')[0]

        # 写入内容拼装
        profile_content = [
            f"用户id：{user_id}",
            f"昵称：{user_info.get('screen_name', user_name)}",
            f"性别：{gender_str}",
            f"生日：{detail_info.get('birthday', '')}",
            f"所在地：{user_info.get('location', '')}",
            f"学习经历：{edu_str}",
            f"工作经历：{career_str}",
            f"阳光信用：{sunshine}",
            f"微博注册时间：{detail_info.get('created_at', '')}",
            f"微博数：{user_info.get('statuses_count', '')}",
            f"关注数：{user_info.get('friends_count', '')}",
            f"粉丝数：{user_info.get('followers_count', '')}",
            f"简介：{desc}",
            f"主页地址：https://weibo.com/u/{user_id}",
            f"头像url：{avatar_clean}",
            f"高清头像url：{avatar_hd_clean}",
            f"微博等级：{urank_str}",
            f"会员等级：{mbrank_str}",
            f"是否认证：{verified_str}",
            f"认证类型：{verified_type_str}",
            f"认证信息：{verified_reason}"
        ]
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(profile_content))
            
        print(f"✅ 成功保存用户 {user_name} 个人资料至 {file_path}")

        # 下载头像文件到本地
        if avatar_clean:
            avatar_local_path = os.path.join(user_dir, "avatar.jpg")
            try:
                download_file(avatar_clean, avatar_local_path, show_progress=False)
                print(f"✅ 成功下载头像到本地: {avatar_local_path}")
            except Exception as e:
                print(f"⚠️ 下载用户头像失败: {e}")
        if avatar_hd_clean:
            avatar_hd_local_path = os.path.join(user_dir, "avatar_hd.jpg")
            try:
                download_file(avatar_hd_clean, avatar_hd_local_path, show_progress=False)
                print(f"✅ 成功下载高清头像到本地: {avatar_hd_local_path}")
            except Exception as e:
                print(f"⚠️ 下载用户高清头像失败: {e}")

        return True
    except Exception as e:
        print(f"❌ 获取用户 {user_name} 个人资料失败: {e}")
        return False

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

    # 1. 递归扫描 SQLite 数据库
    if ENABLE_SAVE_SQLITE:
        try:
            import sqlite3
            db_count = 0
            for root, dirs, files in os.walk(user_dir):
                for file in files:
                    if file.startswith("posts") and file.endswith(".db"):
                        db_path = os.path.join(root, file)
                        try:
                            conn = sqlite3.connect(db_path)
                            cursor = conn.cursor()
                            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='posts'")
                            if cursor.fetchone():
                                cursor.execute("SELECT id FROM posts")
                                for row in cursor.fetchall():
                                    if row[0]:
                                        scraped_ids.add(str(row[0]).strip())
                            conn.close()
                            db_count += 1
                        except Exception as e:
                            print(f"⚠️ 读取 SQLite 文件 '{db_path}' 失败: {e}")
            if db_count > 0:
                print(f"ℹ️ 从 {db_count} 个 SQLite 数据库中加载了 {len(scraped_ids)} 个已抓取的微博 ID")
        except Exception as e:
            print(f"⚠️ 扫描 SQLite 目录失败: {e}")

    # 2. 递归扫描 CSV 文件 (作为补充)
    if ENABLE_SAVE_CSV:
        try:
            csv_count = 0
            for root, dirs, files in os.walk(user_dir):
                for file in files:
                    if file.startswith("posts") and file.endswith(".csv"):
                        csv_path = os.path.join(root, file)
                        try:
                            df = pd.read_csv(csv_path, dtype={"id": str})
                            if "id" in df.columns:
                                for val in df["id"].dropna():
                                    scraped_ids.add(str(val).strip())
                            csv_count += 1
                        except Exception as e:
                            print(f"⚠️ 读取 CSV 文件 '{csv_path}' 失败: {e}")
            if csv_count > 0:
                print(f"ℹ️ 从 {csv_count} 个 CSV 文件加载后，共有 {len(scraped_ids)} 个已抓取的微博 ID")
        except Exception as e:
            print(f"⚠️ 扫描 CSV 目录失败: {e}")

    # 3. 递归扫描 Markdown 文件 (作为兜底)
    if not scraped_ids and ENABLE_SAVE_MARKDOWN:
        try:
            md_count = 0
            for root, dirs, files in os.walk(user_dir):
                for file in files:
                    if file.endswith(".md"):
                        md_path = os.path.join(root, file)
                        try:
                            posts = parse_markdown_posts(md_path)
                            for p in posts:
                                if p.get("id"):
                                    scraped_ids.add(str(p["id"]).strip())
                            md_count += 1
                        except Exception as e:
                            print(f"⚠️ 读取 Markdown 文件 '{md_path}' 失败: {e}")
            if md_count > 0:
                print(f"ℹ️ 从 {md_count} 个 Markdown 文件中加载了 {len(scraped_ids)} 个已抓取的微博 ID")
        except Exception as e:
            print(f"⚠️ 扫描 Markdown 目录失败: {e}")

    return scraped_ids


def scrape_weibo_search():
    # 校验存储格式：Markdown、CSV、JSON、SQLite 必须最少启用一项
    if not (ENABLE_SAVE_MARKDOWN or ENABLE_SAVE_CSV or ENABLE_SAVE_JSON or ENABLE_SAVE_SQLITE):
        print("Error: Markdown, CSV, JSON, SQLite storage formats must have at least one enabled!")
        sys.exit(1)

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
            
            # 获取用户详细资料并保存至 weibo/用户名/用户id.txt
            scrape_and_save_user_profile(page, user_id, user_name)
            
            # 加载已存在的微博 ID 进行增量去重判定
            scraped_ids = load_existing_post_ids(user_name)
            
            # 记录本次抓取的起始时间点作为下一次增量的起点
            run_start_time = datetime.now().replace(microsecond=0)
            
            # 计算该用户的抓取时间范围
            if user_info["start_time"] is not None:
                # 增量抓取起点：直接使用上次成功抓取的时间，去除了 lookback 回溯机制
                user_start_dt = user_info["start_time"]
                user_start_date_str = user_start_dt.strftime("%Y-%m-%d")
            else:
                if START_DATE:
                    user_start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
                    user_start_date_str = START_DATE
                else:
                    user_start_dt = run_start_time
                    user_start_date_str = run_start_time.strftime("%Y-%m-%d")
            
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
                len_before = len(all_posts)
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
                            # 确定是否是转发微博
                            is_retweet = card.locator("div.card-comment").is_visible()
                            
                            # 7. 原创微博过滤
                            if ONLY_ORIGINAL == 1 and is_retweet:
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
                                
                            # 提取发布设备 (device) 和发布位置 (ip_location)
                            device = ""
                            ip_location = ""

                            # 1. 尝试从新版 React 布局提取 (基于类名特征)
                            ip_el = card.locator("div[class*='_ip_']").first
                            if ip_el.is_visible():
                                ip_text = ip_el.get_attribute("title") or ip_el.inner_text()
                                if ip_text:
                                    device_raw = ip_text.replace("发布于", "").strip()
                                    if device_raw:
                                        ip_location = device_raw

                            source_el = card.locator("div[class*='_source_']").first
                            if source_el.is_visible():
                                source_text = source_el.get_attribute("title") or source_el.inner_text()
                                if source_text:
                                    device_raw = source_text.replace("来自", "").strip()
                                    if device_raw:
                                        device = device_raw

                            # 2. 从传统 s.weibo.com 的 p.from 进行解析和兜底
                            if from_el.is_visible():
                                from_text = from_el.inner_text()
                                
                                # 提取位置
                                if not ip_location:
                                    ip_match = re.search(r"(?:发布于|IP属地[：:])\s*(\S+)", from_text)
                                    if ip_match:
                                        ip_location = ip_match.group(1).strip()
                                
                                # 提取设备
                                if not device:
                                    source_match = re.search(r"来自\s*(.+)$", from_text)
                                    if source_match:
                                        device_raw = source_match.group(1).strip()
                                        if "发布于" in device_raw:
                                            device_raw = device_raw.split("发布于")[0].strip()
                                        elif "IP属地" in device_raw:
                                            device_raw = device_raw.split("IP属地")[0].strip()
                                        device = device_raw

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
                                    print(f"  -> 跳过早于抓取起点 ({user_start_dt}) 的微博: {post_time}")
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
                            should_save_images = (RETWEET_PIC_DOWNLOAD if is_retweet else ORIGINAL_PIC_DOWNLOAD)
                            if should_save_images:
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
                            should_save_videos = (RETWEET_VIDEO_DOWNLOAD if is_retweet else ORIGINAL_VIDEO_DOWNLOAD)
                            if should_save_videos:
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
                            should_save_livephotos = (RETWEET_LIVE_PHOTO_DOWNLOAD if is_retweet else ORIGINAL_LIVE_PHOTO_DOWNLOAD)
                            if should_save_livephotos and images:
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
                            
                            # 5.8 提取评论区
                            comments = []
                            if ENABLE_SCRAPE_COMMENTS and post_id:
                                try:
                                    comments = scrape_comments(page, post_id, user_id)
                                except Exception as c_err:
                                    print(f"爬取评论失败 (ID: {post_id}): {c_err}")

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
                                "livephotos": livephotos,
                                "comments": comments,
                                "device": device,
                                "ip_location": ip_location,
                                "is_retweet": is_retweet
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
                if len(all_posts) > len_before:
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


def save_to_csv(data, user_name, target_dir=None, suffix=""):
    """
    将用户的抓取结果保存到 weibo/用户名/posts.csv 文本文件中。
    使用增量更新机制：如果文件已存在，先读取旧数据进行合并去重后再写入。
    """
    if not data:
        return
    csv_dir = target_dir if target_dir else os.path.join(OUTPUT_DIR, user_name)
    os.makedirs(csv_dir, exist_ok=True)
    csv_name = f"posts_{suffix}.csv" if suffix else "posts.csv"
    csv_path = os.path.join(csv_dir, csv_name)
    
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
            "livephotos": ",".join(post.get("livephotos", [])),
            "device": post.get("device", ""),
            "ip_location": post.get("ip_location", "")
        })
        
    df_new = pd.DataFrame(df_data)
    
    if os.path.exists(csv_path):
        try:
            df_old = pd.read_csv(csv_path, dtype={"id": str})
            df_old["id"] = df_old["id"].astype(str)
            # Ensure old columns exist
            for col in ["device", "ip_location"]:
                if col not in df_old.columns:
                    df_old[col] = ""
            df_combined = pd.concat([df_new, df_old]).drop_duplicates(subset=["id"], keep="first")
            df_combined = df_combined.sort_values(by="time", ascending=True)
            df_combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
            print("CSV updated.")
        except Exception as e:
            print(f"Error merging old CSV: {e}, rewriting.")
            df_new.to_csv(csv_path, index=False, encoding="utf-8-sig")
            print("CSV written.")
    else:
        df_new.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print("CSV written.")
        
    # Comments CSV
    if ENABLE_SCRAPE_COMMENTS:
        comments_data = []
        for post in data:
            post_time_val = post.get("time")
            post_time_str = post_time_val.strftime("%Y-%m-%d %H:%M:%S") if isinstance(post_time_val, datetime) else str(post_time_val)
            post_content = post.get("content", "")
            post_summary = post_content[:20].replace("\n", " ").strip() + ("..." if len(post_content) > 20 else "")
            
            if "comments" in post:
                for c in post["comments"]:
                    comments_data.append({
                        "id": str(c.get("id")),
                        "post_id": str(c.get("post_id")),
                        "parent_id": "",
                        "time": c.get("time"),
                        "user_id": str(c.get("user_id")),
                        "user_name": c.get("user_name"),
                        "content": c.get("content"),
                        "like_count": c.get("like_count", 0),
                        "media_url": c.get("media_url", ""),
                        "post_time": post_time_str,
                        "post_summary": post_summary,
                        "source": c.get("source", "")
                    })
                    if "replies" in c:
                        for r in c["replies"]:
                            comments_data.append({
                                "id": str(r.get("id")),
                                "post_id": str(r.get("post_id")),
                                "parent_id": str(r.get("parent_id")),
                                "time": r.get("time"),
                                "user_id": str(r.get("user_id")),
                                "user_name": r.get("user_name"),
                                "content": r.get("content"),
                                "like_count": r.get("like_count", 0),
                                "media_url": r.get("media_url", ""),
                                "post_time": post_time_str,
                                "post_summary": post_summary,
                                "source": r.get("source", "")
                            })
        if comments_data:
            comments_csv_name = f"comments_{suffix}.csv" if suffix else "comments.csv"
            comments_csv_path = os.path.join(csv_dir, comments_csv_name)
            df_comments_new = pd.DataFrame(comments_data)
            if os.path.exists(comments_csv_path):
                try:
                    df_comments_old = pd.read_csv(comments_csv_path, dtype={"id": str, "post_id": str, "parent_id": str, "user_id": str})
                    df_comments_old["id"] = df_comments_old["id"].astype(str)
                    if "source" not in df_comments_old.columns:
                        df_comments_old["source"] = ""
                    df_comments_combined = pd.concat([df_comments_new, df_comments_old]).drop_duplicates(subset=["id"], keep="first")
                    df_comments_combined = df_comments_combined.sort_values(by="time", ascending=True)
                    df_comments_combined.to_csv(comments_csv_path, index=False, encoding="utf-8-sig")
                    print("Comments CSV updated.")
                except Exception as e:
                    print(f"Error merging old comments CSV: {e}, rewriting.")
                    df_comments_new.to_csv(comments_csv_path, index=False, encoding="utf-8-sig")
                    print("Comments CSV written.")
            else:
                df_comments_new.to_csv(comments_csv_path, index=False, encoding="utf-8-sig")
                print("Comments CSV written.")


def save_to_sqlite(data, user_name, target_dir=None, suffix=""):
    """
    将用户的抓取结果写入 weibo/用户名/posts.db 本地 SQLite 数据库中。
    """
    if not data:
        return
    import sqlite3
    import json
    
    db_dir = target_dir if target_dir else os.path.join(OUTPUT_DIR, user_name)
    os.makedirs(db_dir, exist_ok=True)
    db_name = f"posts_{suffix}.db" if suffix else "posts.db"
    db_path = os.path.join(db_dir, db_name)
    
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
            livephotos TEXT,
            device TEXT,
            ip_location TEXT
        )
    """)
    conn.commit()
    
    # 动态扩容以防止已有数据库报错
    for col in ["device", "ip_location"]:
        try:
            cursor.execute(f"ALTER TABLE posts ADD COLUMN {col} TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass
            
    if ENABLE_SCRAPE_COMMENTS:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                post_id TEXT,
                parent_id TEXT,
                time TEXT,
                user_id TEXT,
                user_name TEXT,
                content TEXT,
                like_count INTEGER,
                media_url TEXT,
                post_time TEXT,
                post_summary TEXT,
                source TEXT,
                FOREIGN KEY (post_id) REFERENCES posts (id)
            )
        """)
        conn.commit()
        # 兼容旧版本的数据库，如果不存在对应字段则动态新增
        for col in ["parent_id", "media_url", "post_time", "post_summary", "source"]:
            try:
                cursor.execute(f"ALTER TABLE comments ADD COLUMN {col} TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass # 已经存在该字段
    
    for post in data:
        post_time_val = post.get("time")
        post_time_str = post_time_val.strftime("%Y-%m-%d %H:%M:%S") if isinstance(post_time_val, datetime) else str(post_time_val)
        post_content = post.get("content", "")
        post_summary = post_content[:20].replace("\n", " ").strip() + ("..." if len(post_content) > 20 else "")

        cursor.execute("""
            INSERT OR REPLACE INTO posts (id, time, link, content, reposts_count, comments_count, attitudes_count, images, videos, livephotos, device, ip_location)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post.get("id"),
            post_time_str,
            post.get("link"),
            post.get("content"),
            post.get("reposts_count", 0),
            post.get("comments_count", 0),
            post.get("attitudes_count", 0),
            json.dumps(post.get("images", [])),
            json.dumps(post.get("videos", [])),
            json.dumps(post.get("livephotos", [])),
            post.get("device", ""),
            post.get("ip_location", "")
        ))
        
        if ENABLE_SCRAPE_COMMENTS and "comments" in post:
            for comment in post["comments"]:
                cursor.execute("""
                    INSERT OR REPLACE INTO comments (id, post_id, parent_id, time, user_id, user_name, content, like_count, media_url, post_time, post_summary, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    comment.get("id"),
                    comment.get("post_id"),
                    None,
                    comment.get("time"),
                    comment.get("user_id"),
                    comment.get("user_name"),
                    comment.get("content"),
                    comment.get("like_count", 0),
                    comment.get("media_url"),
                    post_time_str,
                    post_summary,
                    comment.get("source", "")
                ))
                if "replies" in comment:
                    for reply in comment["replies"]:
                        cursor.execute("""
                            INSERT OR REPLACE INTO comments (id, post_id, parent_id, time, user_id, user_name, content, like_count, media_url, post_time, post_summary, source)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            reply.get("id"),
                            reply.get("post_id"),
                            reply.get("parent_id"),
                            reply.get("time"),
                            reply.get("user_id"),
                            reply.get("user_name"),
                            reply.get("content"),
                            reply.get("like_count", 0),
                            reply.get("media_url"),
                            post_time_str,
                            post_summary,
                            reply.get("source", "")
                        ))
        
    conn.commit()
    conn.close()
    print(f"SQLite synced: {db_path}")


def save_to_json(data, user_name, target_dir=None, suffix=""):
    """
    将用户的抓取结果保存到 JSON 文件中。
    """
    if not data:
        return
    import json
    
    json_dir = target_dir if target_dir else os.path.join(OUTPUT_DIR, user_name)
    os.makedirs(json_dir, exist_ok=True)
    json_name = f"posts_{suffix}.json" if suffix else "posts.json"
    json_path = os.path.join(json_dir, json_name)
    
    new_json_data = []
    for post in data:
        post_time_str = post.get("time").strftime("%Y-%m-%d %H:%M:%S") if isinstance(post.get("time"), datetime) else str(post.get("time"))
        
        post_item = {
            "id": str(post.get("id")),
            "time": post_time_str,
            "link": post.get("link"),
            "content": post.get("content"),
            "reposts_count": post.get("reposts_count", 0),
            "comments_count": post.get("comments_count", 0),
            "attitudes_count": post.get("attitudes_count", 0),
            "images": post.get("images", []),
            "videos": post.get("videos", []),
            "livephotos": post.get("livephotos", []),
            "device": post.get("device", ""),
            "ip_location": post.get("ip_location", "")
        }
        if "comments" in post:
            post_item["comments"] = post["comments"]
            
        new_json_data.append(post_item)
        
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                old_json_data = json.load(f)
            
            combined_dict = {str(item["id"]): item for item in old_json_data}
            for item in new_json_data:
                combined_dict[str(item["id"])] = item
                
            merged_list = list(combined_dict.values())
            merged_list.sort(key=lambda x: x.get("time", ""))
            
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(merged_list, f, ensure_ascii=False, indent=2)
            print("JSON updated.")
            return
        except Exception as e:
            print(f"Error merging JSON: {e}, rewriting.")
            
    new_json_data.sort(key=lambda x: x.get("time", ""))
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(new_json_data, f, ensure_ascii=False, indent=2)
        print("JSON written.")
    except Exception as e:
        print(f"Error saving JSON: {e}")


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
        
        os.makedirs(month_dir, exist_ok=True)
        
        file_name = f"{date_str}.md"
        file_path = os.path.join(month_dir, file_name)
        
        # 1. 组装新爬取微博的 Markdown 部分并下载对应的多媒体文件
        new_md_posts = []
        for post in new_posts:
            time_display = post['time'].strftime('%H:%M:%S')
            post_id = post.get("id", str(post['time']))
            safe_post_id = re.sub(r'[^\w\-]', '_', post_id)
            time_prefix = post['time'].strftime('%Y%m%d_%H%M%S')
            
            is_retweet = post.get("is_retweet", False)
            if is_retweet:
                img_dir = os.path.join(month_dir, "retweet", "img")
                video_dir = os.path.join(month_dir, "retweet", "video")
                livephoto_dir = os.path.join(month_dir, "retweet", "livephoto")
                comment_media_dir = os.path.join(month_dir, "retweet", "comment")
                
                img_rel = "./retweet/img"
                video_rel = "./retweet/video"
                livephoto_rel = "./retweet/livephoto"
                comment_rel = "./retweet/comment"
            else:
                img_dir = os.path.join(month_dir, "img")
                video_dir = os.path.join(month_dir, "video")
                livephoto_dir = os.path.join(month_dir, "livephoto")
                comment_media_dir = os.path.join(month_dir, "comment")
                
                img_rel = "./img"
                video_rel = "./video"
                livephoto_rel = "./livephoto"
                comment_rel = "./comment"
            
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
            
            # 基本信息 (发布设备和发布位置)
            device_val = post.get("device", "")
            location_val = post.get("ip_location", "")
            if device_val or location_val:
                body_lines.append(f"**基本信息:** 发布设备: `{device_val}` | 发布位置: `{location_val}`\n")
            
            body_lines.append(f"{post['content']}\n")
            
            # 保存图片逻辑
            if ENABLE_SAVE_IMAGES and post.get("images"):
                os.makedirs(img_dir, exist_ok=True)
                local_image_paths = [None] * len(post["images"])
                tasks = []
                for idx, img_url in enumerate(post["images"]):
                    ext = "jpg"
                    ext_match = re.search(r'\.(\w+)(?:\?|$)', img_url)
                    if ext_match:
                        ext = ext_match.group(1)
                    
                    img_name = f"{time_prefix}_{safe_post_id}_{idx + 1}.{ext}"
                    img_save_path = os.path.join(img_dir, img_name)
                    local_path = f"{img_rel}/{img_name}"
                    
                    if not os.path.exists(img_save_path):
                        tasks.append({
                            "idx": idx,
                            "url": img_url,
                            "save_path": img_save_path,
                            "local_path": local_path,
                            "desc": f"图片 {idx + 1}/{len(post['images'])}"
                        })
                    else:
                        local_image_paths[idx] = local_path
                
                if tasks:
                    from concurrent.futures import ThreadPoolExecutor
                    max_workers = DOWNLOAD_NUM_CONCURRENT_MEDIA
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        def worker(task):
                            show_prog = (DOWNLOAD_NUM_CONCURRENT_MEDIA == 1)
                            if show_prog:
                                print(f"正在下载{task['desc']}: {task['url']}")
                            else:
                                print(f"正在下载{task['desc']}...")
                            success = download_file(task["url"], task["save_path"], page, show_progress=show_prog)
                            return task, success
                        
                        for task, success in executor.map(worker, tasks):
                            if success:
                                local_image_paths[task["idx"]] = task["local_path"]
                                
                local_image_paths = [p for p in local_image_paths if p is not None]
                if local_image_paths:
                    body_lines.append("")
                    for local_path in local_image_paths:
                        body_lines.append(f"![image]({local_path})")
                    body_lines.append("")
                        
            # 保存视频逻辑
            video_urls_to_download = post.get("download_videos") if post.get("download_videos") is not None else post.get("videos", [])
            if ENABLE_SAVE_VIDEOS and video_urls_to_download:
                os.makedirs(video_dir, exist_ok=True)
                local_video_paths = [None] * len(video_urls_to_download)
                tasks = []
                for idx, video_url in enumerate(video_urls_to_download):
                    if "video.weibo.com" in video_url or "weibo.com/tv" in video_url:
                        continue
                    ext = "mp4"
                    ext_match = re.search(r'\.(\w+)(?:\?|$)', video_url)
                    if ext_match:
                        ext = ext_match.group(1)
                    
                    video_name = f"{time_prefix}_{safe_post_id}_{idx + 1}.{ext}"
                    video_save_path = os.path.join(video_dir, video_name)
                    local_path = f"{video_rel}/{video_name}"
                    
                    if not os.path.exists(video_save_path):
                        tasks.append({
                            "idx": idx,
                            "url": video_url,
                            "save_path": video_save_path,
                            "local_path": local_path,
                            "desc": f"视频 {idx + 1}/{len(video_urls_to_download)}"
                        })
                    else:
                        local_video_paths[idx] = local_path
                
                if tasks:
                    from concurrent.futures import ThreadPoolExecutor
                    max_workers = DOWNLOAD_NUM_CONCURRENT_MEDIA
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        def worker(task):
                            show_prog = (DOWNLOAD_NUM_CONCURRENT_MEDIA == 1)
                            if show_prog:
                                print(f"正在下载{task['desc']}: {task['url']}")
                            else:
                                print(f"正在下载{task['desc']}...")
                            success = download_file(task["url"], task["save_path"], page, show_progress=show_prog)
                            return task, success
                        
                        for task, success in executor.map(worker, tasks):
                            if success:
                                local_video_paths[task["idx"]] = task["local_path"]
                                
                local_video_paths = [p for p in local_video_paths if p is not None]
                if local_video_paths:
                    body_lines.append("")
                    for local_path in local_video_paths:
                        body_lines.append(f'<video src="{local_path}" controls width="100%"></video>')
                    body_lines.append("")
                        
            # 保存实况照片 (Live Photo) 逻辑
            if ENABLE_SAVE_LIVEPHOTOS and post.get("livephotos"):
                os.makedirs(livephoto_dir, exist_ok=True)
                local_livephoto_paths = [None] * len(post["livephotos"])
                tasks = []
                for idx, lp_url in enumerate(post["livephotos"]):
                    ext = "mov"
                    ext_match = re.search(r'\.(\w+)(?:\?|$)', lp_url)
                    if ext_match:
                        ext = ext_match.group(1)
                    
                    lp_name = f"{time_prefix}_{safe_post_id}_{idx + 1}.{ext}"
                    lp_save_path = os.path.join(livephoto_dir, lp_name)
                    local_path = f"{livephoto_rel}/{lp_name}"
                    
                    if not os.path.exists(lp_save_path):
                        tasks.append({
                            "idx": idx,
                            "url": lp_url,
                            "save_path": lp_save_path,
                            "local_path": local_path,
                            "desc": f"实况视频 {idx + 1}/{len(post['livephotos'])}"
                        })
                    else:
                        local_livephoto_paths[idx] = local_path
                        
                if tasks:
                    from concurrent.futures import ThreadPoolExecutor
                    max_workers = DOWNLOAD_NUM_CONCURRENT_MEDIA
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        def worker(task):
                            show_prog = (DOWNLOAD_NUM_CONCURRENT_MEDIA == 1)
                            if show_prog:
                                print(f"正在下载{task['desc']}: {task['url']}")
                            else:
                                print(f"正在下载{task['desc']}...")
                            success = download_file(task["url"], task["save_path"], page, show_progress=show_prog)
                            return task, success
                        
                        for task, success in executor.map(worker, tasks):
                            if success:
                                local_livephoto_paths[task["idx"]] = task["local_path"]
                                
                local_livephoto_paths = [p for p in local_livephoto_paths if p is not None]
                if local_livephoto_paths:
                    body_lines.append("\n*实况照片动效视频:*")
                    for local_path in local_livephoto_paths:
                        body_lines.append(f'<video src="{local_path}" controls width="100%"></video>')
                    body_lines.append("")

            # 保存评论与回复逻辑 (带图片下载与缩进排版)
            if ENABLE_SCRAPE_COMMENTS and post.get("comments"):
                comment_tasks = []
                media_path_map = {}
                
                # 先收集所有的评论媒体下载任务，进行并发下载
                for c in post["comments"]:
                    c_id = c['id']
                    c_media = c.get("media_url", "")
                    if ENABLE_SAVE_COMMENT_MEDIA and c_media:
                        os.makedirs(comment_media_dir, exist_ok=True)
                        ext = "jpg"
                        ext_match = re.search(r'\.(\w+)(?:\?|$)', c_media)
                        if ext_match:
                            ext = ext_match.group(1)
                        c_media_name = f"{time_prefix}_{safe_post_id}_comment_{c_id}.{ext}"
                        c_media_path = os.path.join(comment_media_dir, c_media_name)
                        local_path = f"{comment_rel}/{c_media_name}"
                        
                        if not os.path.exists(c_media_path):
                            comment_tasks.append({
                                "id": c_id,
                                "url": c_media,
                                "save_path": c_media_path,
                                "local_path": local_path,
                                "desc": f"评论图片"
                            })
                        else:
                            media_path_map[c_id] = local_path
                            
                    if c.get("replies"):
                        for r in c["replies"]:
                            r_id = r['id']
                            r_media = r.get("media_url", "")
                            if ENABLE_SAVE_COMMENT_MEDIA and r_media:
                                os.makedirs(comment_media_dir, exist_ok=True)
                                ext = "jpg"
                                ext_match = re.search(r'\.(\w+)(?:\?|$)', r_media)
                                if ext_match:
                                    ext = ext_match.group(1)
                                r_media_name = f"{time_prefix}_{safe_post_id}_comment_{r_id}.{ext}"
                                r_media_path = os.path.join(comment_media_dir, r_media_name)
                                local_path = f"{comment_rel}/{r_media_name}"
                                
                                if not os.path.exists(r_media_path):
                                    comment_tasks.append({
                                        "id": r_id,
                                        "url": r_media,
                                        "save_path": r_media_path,
                                        "local_path": local_path,
                                        "desc": f"回复图片"
                                    })
                                else:
                                    media_path_map[r_id] = local_path
                                    
                if comment_tasks:
                    from concurrent.futures import ThreadPoolExecutor
                    max_workers = DOWNLOAD_NUM_CONCURRENT_MEDIA
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        def worker(task):
                            show_prog = (DOWNLOAD_NUM_CONCURRENT_MEDIA == 1)
                            if show_prog:
                                print(f"正在下载{task['desc']}: {task['url']}")
                            else:
                                print(f"正在下载{task['desc']}...")
                            success = download_file(task["url"], task["save_path"], page, show_progress=show_prog)
                            return task, success
                        
                        for task, success in executor.map(worker, comment_tasks):
                            if success:
                                media_path_map[task["id"]] = task["local_path"]
                                
                body_lines.append("\n**评论区:**")
                for c in post["comments"]:
                    c_content = c['content']
                    c_like = c['like_count']
                    c_time = c['time']
                    c_user = c['user_name']
                    c_id = c['id']
                    
                    local_c_media = media_path_map.get(c_id, "")
                    
                    c_source = c.get("source", "")
                    source_suffix = f" {c_source}" if c_source else ""
                    
                    # 调整 (赞 xx) 的位置到内容后、时间前
                    body_lines.append(f"- **{c_user}**: {c_content}{source_suffix}  (赞 {c_like}) *({c_time})*")
                    if local_c_media:
                        body_lines.append(f"  ![comment_image]({local_c_media})")
                        
                    if c.get("replies"):
                        for r in c["replies"]:
                            r_content = r['content']
                            r_like = r['like_count']
                            r_time = r['time']
                            r_user = r['user_name']
                            r_id = r['id']
                            
                            local_r_media = media_path_map.get(r_id, "")
                            
                            r_source = r.get("source", "")
                            r_source_suffix = f" {r_source}" if r_source else ""
                            
                            body_lines.append(f"  - **{r_user}** 回复 **{c_user}**: {r_content}{r_source_suffix}  (赞 {r_like}) *({r_time})*")
                            if local_r_media:
                                body_lines.append(f"    ![comment_image]({local_r_media})")
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
    
    # 额外存储为 CSV、SQLite 数据库和 JSON
    # 1. 全局累计存储 (若为 "global" 或 "both")
    if SAVE_DATA_BY_PERIOD in ("global", "both"):
        if ENABLE_SAVE_CSV:
            try:
                save_to_csv(data, user_name)
            except Exception as csv_err:
                print(f"保存全局 CSV 失败: {csv_err}")
                
        if ENABLE_SAVE_SQLITE:
            try:
                save_to_sqlite(data, user_name)
            except Exception as sqlite_err:
                print(f"保存全局 SQLite 失败: {sqlite_err}")
    
        if ENABLE_SAVE_JSON:
            try:
                save_to_json(data, user_name)
            except Exception as json_err:
                print(f"保存全局 JSON 失败: {json_err}")
                
    # 2. 按月分割存储 (若为 "monthly" 或 "both")
    if SAVE_DATA_BY_PERIOD in ("monthly", "both"):
        # 按月分组
        posts_by_month = {}
        for post in data:
            month_str = post['time'].strftime('%Y-%m')
            if month_str not in posts_by_month:
                posts_by_month[month_str] = []
            posts_by_month[month_str].append(post)
            
        for month_str, month_posts in posts_by_month.items():
            month_dir = os.path.join(OUTPUT_DIR, user_name, month_str)
            os.makedirs(month_dir, exist_ok=True)
            
            if ENABLE_SAVE_CSV:
                try:
                    save_to_csv(month_posts, user_name, target_dir=month_dir, suffix=month_str)
                except Exception as csv_err:
                    print(f"保存月度 CSV ({month_str}) 失败: {csv_err}")
                    
            if ENABLE_SAVE_SQLITE:
                try:
                    save_to_sqlite(month_posts, user_name, target_dir=month_dir, suffix=month_str)
                except Exception as sqlite_err:
                    print(f"保存月度 SQLite ({month_str}) 失败: {sqlite_err}")
                    
            if ENABLE_SAVE_JSON:
                try:
                    save_to_json(month_posts, user_name, target_dir=month_dir, suffix=month_str)
                except Exception as json_err:
                    print(f"保存月度 JSON ({month_str}) 失败: {json_err}")
            

if __name__ == "__main__":
    scrape_weibo_search()
