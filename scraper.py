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

import builtins
import logging
from config import ENABLE_VERBOSE_LOGGING, ENABLE_FILE_LOGGING, LOG_FILE_PATH

logger = logging.getLogger('weibo_scraper')
logger.setLevel(logging.INFO)

if getattr(config, 'ENABLE_FILE_LOGGING', 1):
    file_handler = logging.FileHandler(LOG_FILE_PATH, encoding='utf-8')
    file_formatter = logging.Formatter('%(asctime)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

original_print = builtins.print

def verbose_print(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    logger.info(msg)
    if ENABLE_VERBOSE_LOGGING:
        original_print(*args, **kwargs)
    elif msg.startswith('错误:') or msg.startswith('[提示]') or '删除' in msg or '清理完成' in msg or '扫描' in msg or '找到' in msg or '跳过' in msg:
        original_print(*args, **kwargs)

builtins.print = verbose_print

from rich.console import Console
from rich.status import Status
global_console = Console()
global_dashboard = Status("[bold cyan]准备开始抓取...[/bold cyan]", console=global_console)

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
COMMENT_PAGE_DELAY = getattr(config, "COMMENT_PAGE_DELAY", 0.3)
REPLY_PAGE_DELAY = getattr(config, "REPLY_PAGE_DELAY", 0.15)
COMMENT_FLOW = getattr(config, "COMMENT_FLOW", 0)
# ----------------


def safe_filename(name):
    """
    过滤掉 Windows/Linux/Mac 中不能用于文件名或路径的安全隐患字符，
    防止恶意路径穿越（如 ../）或由于特殊字符导致程序越界崩溃。
    """
    if not name:
        return "unknown"
    # 替换 / \ : * ? " < > | 为下划线
    name = re.sub(r'[\\/:*?"<>|]', '_', str(name))
    # 防止路径穿越
    name = name.replace("..", "_")
    return name.strip()


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
            except Exception:
                return 0
        try:
            return int(num_str)
        except Exception:
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
            pass
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
        with urllib.request.urlopen(req, timeout=15) as conn:
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
def clean_html_preserve_emojis(html_str):
    if not html_str:
        return ""
    import re
    import html
    # 提取 <img alt="[xxx]"> 中的 [xxx] 作为表情纯文本
    text = re.sub(r'<img[^>]*?alt=["\'](\[[^"\'\]]+\])["\'][^>]*?>', r'\1', html_str)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()

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
    max_id_type = 0
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
            url += f"&max_id={max_id}&max_id_type={max_id_type}"
            
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
                reply_text = clean_html_preserve_emojis(reply_text_raw)
                
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
            max_id_type = res_json.get("max_id_type", 0)
            if max_id == 0:
                break
            # 适当延时防反爬
            time.sleep(REPLY_PAGE_DELAY)
            
        except Exception as e:
            print(f"      ⚠️ 爬取楼中楼出错 (ID: {comment_id}): {e}")
            break
            
    return replies

def scrape_comments(page, post_id, target_user_id=""):
    """
    通过真实浏览器网络响应拦截爬取一条微博的评论，并包含子评论（楼中楼）。
    这种方式可以完全模拟人工刷微博的行为，最大程度绕过隐式反爬获取完整评论。
    返回主评论列表，格式: [{"id": "...", "post_id": "...", "time": "...", "user_id": "...", "user_name": "...", "content": "...", "like_count": 0, "media_url": "...", "replies": [...]}]
    """
    if not post_id:
        return []
        
    print(f"  -> 开始使用页面滚动拦截网络请求方式爬取微博 {post_id} 的评论区...")
    
    context = page.context
    comment_page = context.new_page()
    detail_url = f"https://weibo.com/detail/{post_id}"
        
    collected_json_data = []
    
    def handle_response(response):
        if "ajax/statuses/buildComments" in response.url and response.status == 200:
            try:
                res_json = response.json()
                if res_json and res_json.get("ok") == 1:
                    collected_json_data.append(res_json.get("data", []))
            except Exception:
                pass
                
    comment_page.on("response", handle_response)
    
    try:
        comment_page.goto(detail_url)
        try:
            comment_page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
            
        time.sleep(3)
        
        # 尝试切换为按时间排序，如果配置项启用了
        if COMMENT_FLOW == 1:
            try:
                # 定位“按时间”按钮，通常是 div 或 span 里面写着“按时间”
                time_sort_btn = comment_page.locator("text='按时间'").first
                if time_sort_btn.is_visible(timeout=3000):
                    time_sort_btn.click()
                    time.sleep(2)
                    print("    已自动切换为按时间排序。")
            except Exception:
                pass

        last_json_count = 0
        scroll_attempts = 0
        max_scroll_attempts = 8 # 稍微减小最大尝试次数，以提高效率
        
        while True:
            # 统计当前已抓取的主评论数，做粗略限制
            current_comments_count = sum(len(d) for d in collected_json_data)
            if current_comments_count >= MAX_COMMENTS_PER_POST:
                print(f"    已拦截到充足的评论 ({current_comments_count})，达到配置上限。停止滚动。")
                break
                
            comment_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.5)
            
            if len(collected_json_data) > last_json_count:
                last_json_count = len(collected_json_data)
                scroll_attempts = 0
            else:
                scroll_attempts += 1
                if scroll_attempts >= max_scroll_attempts:
                    print(f"    连续 {max_scroll_attempts} 次滚动未获取到新评论数据，判断评论区已到底部。")
                    break
    except Exception as e:
        print(f"    ⚠️ 滚动拦截评论时页面出错: {e}")
    finally:
        try:
            comment_page.close()
        except Exception:
            pass
            
    # 去重并解析捕获到的 JSON 数据
    comments_dict = {}
    
    for data in collected_json_data:
        for item in data:
            comment_id = str(item.get("id"))
            if comment_id in comments_dict:
                continue
                
            if len(comments_dict) >= MAX_COMMENTS_PER_POST:
                break
                
            comment_text_raw = item.get("text", "")
            comment_text = clean_html_preserve_emojis(comment_text_raw)
            
            user_info = item.get("user", {})
            user_id = str(user_info.get("id", ""))
            user_name = user_info.get("screen_name", "未知用户")
            
            created_at_str = item.get("created_at", "")
            comment_time = ""
            if created_at_str:
                try:
                    dt = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
                    comment_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    comment_time = str(created_at_str)
            
            like_count = item.get("like_counts", 0)
            
            # 解析随附的楼中楼数据
            replies = []
            raw_replies = item.get("comments", [])
            total_replies_cnt = item.get("total_number", 0)
            
            # 如果配置要求爬取更多楼中楼，且当前随附的楼中楼没给够，采用 API 原路补充拉取
            if total_replies_cnt > len(raw_replies) and len(raw_replies) < MAX_REPLIES_PER_COMMENT:
                replies = scrape_replies(page, post_id, comment_id, target_user_id)
            else:
                for r_item in raw_replies:
                    r_id = str(r_item.get("id"))
                    r_text_raw = r_item.get("text", "")
                    r_text = clean_html_preserve_emojis(r_text_raw)
                    
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
            comments_dict[comment_id] = {
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
            }
            
    comments = list(comments_dict.values())
    total_count = len(comments) + sum(len(c.get("replies", [])) for c in comments)
    print(f"  -> 微博 {post_id} 成功爬取 {total_count} 条评论 (主评论 {len(comments)} 条，子回复 {total_count - len(comments)} 条)。")
    return comments

def fetch_user_name(page, user_id):
    """
    优先通过直接 API 接口请求获取用户昵称，若失败则访问用户微博主页自动获取。
    如果获取失败，回退使用用户 ID。
    """
    info_url = f"https://weibo.com/ajax/profile/info?uid={user_id}"
    headers = {
        "Referer": f"https://weibo.com/u/{user_id}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # 1. 尝试直接使用 HTTP API 快速获取昵称
    try:
        response = page.context.request.get(info_url, headers=headers)
        if response.status == 200:
            info_res = response.json()
            if info_res.get("ok") == 1:
                screen_name = info_res.get("data", {}).get("user", {}).get("screen_name")
                if screen_name:
                    print(f"✅ 获取到用户昵称: {screen_name}")
                    return screen_name
    except Exception as api_err:
        print(f"⚠️ 获取用户昵称失败: {api_err}，将尝试页面加载兜底。")

    # 2. 页面加载兜底流程
    profile_url = f"https://weibo.com/u/{user_id}"
    print(f"\n正在通过页面获取用户昵称: {profile_url}")
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector("div[class^='_name_']", timeout=3000)
        
        # 优先通过 API 接口获取昵称
        try:
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
    user_dir = os.path.join(OUTPUT_DIR, safe_filename(user_name))
    os.makedirs(user_dir, exist_ok=True)
    file_path = os.path.join(user_dir, f"{user_id}.txt")
    
    if os.path.exists(file_path):
        print(f"ℹ️ 用户 {user_name} ({user_id}) 的个人资料 {user_id}.txt 已存在，跳过重复提取下载。")
        return user_name
        
    print(f"正在获取用户 {user_name} ({user_id}) 的详细个人信息...")
    
    info_url = f"https://weibo.com/ajax/profile/info?uid={user_id}"
    detail_url = f"https://weibo.com/ajax/profile/detail?uid={user_id}"
    headers = {
        "Referer": f"https://weibo.com/u/{user_id}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    user_info = {}
    detail_info = {}
    success = False

    # 1. 尝试直接使用 HTTP API 快速获取用户资料
    try:
        info_res_obj = page.context.request.get(info_url, headers=headers)
        detail_res_obj = page.context.request.get(detail_url, headers=headers)
        
        if info_res_obj.status == 200 and detail_res_obj.status == 200:
            info_res = info_res_obj.json()
            detail_res = detail_res_obj.json()
            if info_res.get("ok") == 1 and detail_res.get("ok") == 1:
                user_info = info_res.get("data", {}).get("user", {})
                detail_info = detail_res.get("data", {})
                if user_info or detail_info:
                    print("✅ 获取到用户资料")
                    success = True
    except Exception as api_err:
        print(f"⚠️ 获取用户资料失败: {api_err}，将尝试页面加载兜底。")

    # 2. 页面加载兜底流程
    if not success:
        try:
            # 访问用户主页以确保 Cookies 初始化和请求在上下文内进行
            profile_url = f"https://weibo.com/u/{user_id}"
            page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1000) # 轻微延时确保 fetch 环境就绪
            
            # 爬取基础资料 info
            info_res = page.evaluate("async (url) => { const r = await fetch(url); return await r.json(); }", info_url)
            user_info = info_res.get("data", {}).get("user", {}) if info_res.get("ok") == 1 else {}
            
            # 爬取详细资料 detail
            detail_res = page.evaluate("async (url) => { const r = await fetch(url); return await r.json(); }", detail_url)
            detail_info = detail_res.get("data", {}) if detail_res.get("ok") == 1 else {}
        except Exception as e:
            print(f"❌ 页面加载获取用户 {user_name} 个人资料失败: {e}")
            return False

    try:
        if not user_info and not detail_info:
            print(f"⚠️ 无法通过 API 接口获取用户 {user_id} 的资料")
            return user_name
            
        # 如果 API 获取到了昵称，且原本传递进来的是用户 ID，则修正 user_name
        if str(user_name) == str(user_id) and user_info.get("screen_name"):
            user_name = str(user_info.get("screen_name")).strip()
            # 重新构建正确的目录
            user_dir = os.path.join(OUTPUT_DIR, safe_filename(user_name))
            os.makedirs(user_dir, exist_ok=True)
            file_path = os.path.join(user_dir, f"{user_id}.txt")
            
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
            
        # 获取手机端背景图 URL
        cover_phone = user_info.get('cover_image_phone', '') or user_info.get('cover_image', '')
        if '?' in cover_phone:
            cover_phone = cover_phone.split('?')[0]
        # 将手机端裁剪尺寸替换为高清原图尺寸
        if cover_phone and '/crop.' in cover_phone:
            import re
            cover_phone = re.sub(r'/crop\.[^/]+/', '/mw2000/', cover_phone)
            
        # 尝试从已经打开的页面 DOM 获取真实的网页端大横幅背景图
        cover_web = ""
        try:
            dom_cover = page.evaluate('''() => {
                const el = document.querySelector('.woo-panel-left > div:first-child img.woo-picture-img');
                return el ? el.src : '';
            }''')
            if dom_cover and ('mw2000' in dom_cover or 'bmiddle' in dom_cover or 'large' in dom_cover or 'orj' in dom_cover):
                # 确保获取的是最高清版本
                if '/orj' in dom_cover:
                    import re
                    dom_cover = re.sub(r'/orj[^/]+/', '/mw2000/', dom_cover)
                cover_web = dom_cover
        except Exception:
            pass

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
            f"手机端背景图url：{cover_phone}",
            f"网页端背景图url：{cover_web}",
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
                
        # 下载手机端背景图
        if cover_phone:
            cover_phone_local_path = os.path.join(user_dir, "cover_image_phone.jpg")
            try:
                download_file(cover_phone, cover_phone_local_path, show_progress=False)
                print(f"✅ 成功下载手机端背景图到本地: {cover_phone_local_path}")
            except Exception as e:
                print(f"⚠️ 下载手机端背景图失败: {e}")
                
        # 下载网页端背景图
        if cover_web and cover_web != cover_phone:
            cover_web_local_path = os.path.join(user_dir, "cover_image_web.jpg")
            try:
                download_file(cover_web, cover_web_local_path, show_progress=False)
                print(f"✅ 成功下载网页端背景图到本地: {cover_web_local_path}")
            except Exception as e:
                print(f"⚠️ 下载网页端背景图失败: {e}")

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
    user_dir = os.path.join(OUTPUT_DIR, safe_filename(user_name))
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


def scrape_weibo_search(scrape_target=None, target_uid=None):
    local_start_date = START_DATE
    local_end_date = END_DATE
    target_mid = None
    target_bid = None
    if scrape_target:
        if re.match(r'^\d{4}-\d{2}-\d{2}$', scrape_target):
            local_start_date = scrape_target
            local_end_date = scrape_target
        elif re.match(r'^\d{2}-\d{2}$', scrape_target):
            print("错误: 不支持仅输入 MM-DD 格式的日期，请使用完整的 YYYY-MM-DD 格式。")
            sys.exit(1)
            
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', scrape_target):
            url_match = re.search(r'weibo\.com/(\d+)/([A-Za-z0-9]+)', scrape_target)
            if url_match:
                target_uid = url_match.group(1)
                scrape_target = url_match.group(2)
    # 校验存储格式：Markdown、CSV、JSON、SQLite 必须最少启用一项
    if not (ENABLE_SAVE_MARKDOWN or ENABLE_SAVE_CSV or ENABLE_SAVE_JSON or ENABLE_SAVE_SQLITE):
        print("错误: 必须在配置中至少启用 Markdown、CSV、JSON 或 SQLite 存储格式中的一种！")
        sys.exit(1)

    if not os.path.exists(STATE_FILE):
        print(f"错误: 未找到 {STATE_FILE}。请先运行 login.py 进行登录。")
        return

    if target_uid and scrape_target:
        user_ids = [{"id": str(target_uid).strip(), "username": None, "start_time": None}]
    elif target_uid:
        user_ids = get_user_ids(TARGET_USER_IDS)
        filtered = [u for u in user_ids if str(u["id"]) == str(target_uid).strip()]
        if filtered:
            user_ids = filtered
        else:
            user_ids = [{"id": str(target_uid).strip(), "username": None, "start_time": None}]
    else:
        user_ids = get_user_ids(TARGET_USER_IDS)

    # 单篇指定逻辑处理
    if scrape_target and not re.match(r'^\d{4}-\d{2}-\d{2}$', scrape_target):
        if user_ids and len(user_ids) == 1:
            if not target_uid:
                u_name = user_ids[0].get("username", "")
                u_name_str = f" ({u_name})" if u_name else ""
                print(f"  -> 提示: 未指定 UID，自动使用当前配置的用户 UID: {user_ids[0]['id']}{u_name_str}")
        else:
            print("错误: 当前配置了多个目标用户。当提供短 ID/BID 时，请通过完整的网页链接，或使用 -u <uid> 参数来明确该微博属于哪个用户！")
            return
            
    if not user_ids:
        print("错误: 未配置有效的目标用户 ID 或未能从 URL 提取 UID。")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        headless_browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        with global_dashboard:
            for user_idx, user_info in enumerate(user_ids, 1):
                user_id = user_info["id"]
                original_print(f"\n==========================================")
                original_print(f"开始抓取第 {user_idx}/{len(user_ids)} 个用户 ID: {user_id}")
                original_print(f"==========================================")

                # 自动获取用户昵称
                user_name = fetch_user_name(page, user_id)
                global_dashboard.update(f"[bold cyan]正在抓取第 {user_idx}/{len(user_ids)} 个用户: {user_name} ({user_id}) | 进度: 初始化...[/bold cyan]")
                
                original_print(f"打开用户 {user_name} ({user_id}) 主页...")
            try:
                page.goto(f"https://weibo.com/u/{user_id}", wait_until="domcontentloaded", timeout=15000)
                # 等待 React 渲染出主体布局（极快），不必等待所有图片和追踪脚本加载
                page.wait_for_selector('.woo-panel-left', timeout=5000)
            except Exception:
                pass

            # 获取用户详细资料并保存至 weibo/用户名/用户id.txt
            real_name = scrape_and_save_user_profile(page, user_id, user_name)
            if real_name and str(real_name) != str(user_name):
                user_name = real_name
                # 更新面板上的名字
                global_dashboard.update(f"[bold cyan]正在抓取第 {user_idx}/{len(user_ids)} 个用户: {user_name} ({user_id}) | 进度: 初始化...[/bold cyan]")
            
            # 加载已存在的微博 ID 进行增量去重判定
            scraped_ids = load_existing_post_ids(user_name)
            
            # 记录本次抓取的起始时间点作为下一次增量的起点
            run_start_time = datetime.now().replace(microsecond=0)
            
            # 计算该用户的抓取时间范围
            if user_info["start_time"] is not None and not scrape_target:
                # 增量抓取起点：直接使用上次成功抓取的时间，去除了 lookback 回溯机制
                user_start_dt = user_info["start_time"]
                user_start_date_str = user_start_dt.strftime("%Y-%m-%d")
            else:
                if local_start_date:
                    user_start_dt = datetime.strptime(local_start_date, "%Y-%m-%d")
                    user_start_date_str = local_start_date
                else:
                    user_start_dt = run_start_time
                    user_start_date_str = run_start_time.strftime("%Y-%m-%d")
            
            if local_end_date:
                user_end_dt = datetime.strptime(local_end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                user_end_date_str = local_end_date
            else:
                user_end_dt = run_start_time
                user_end_date_str = run_start_time.strftime("%Y-%m-%d")
            
            if scrape_target and not re.match(r'^\d{4}-\d{2}-\d{2}$', scrape_target):
                user_date_ranges = [("SINGLE_POST", "SINGLE_POST")]
            else:
                user_date_ranges = get_date_ranges(user_start_date_str, user_end_date_str, step_days=1)
                print(f"时间段切分为 {len(user_date_ranges)} 个时间范围进行搜索。")
            
            # 每个用户的独立去重与存储
            scraped_count = 0
            processed_ids = set() # 用于去重

            for start_str, end_str in user_date_ranges:
                initial_scraped_count = scraped_count
                if start_str == "SINGLE_POST":
                    print(f"\n=== 用户 {user_name} ({user_id}) | 开始抓取指定微博 ===")
                else:
                    print(f"\n=== 用户 {user_name} ({user_id}) | 开始抓取时间段: {start_str} 至 {end_str} ===")
                
                date_info = f"指定微博 ID: {scrape_target}" if start_str == "SINGLE_POST" else (f"日期: {start_str}" if start_str == end_str else f"日期: {start_str} 至 {end_str}")
                global_dashboard.update(f"[bold cyan]正在抓取第 {user_idx}/{len(user_ids)} 个用户: {user_name} ({user_id}) | {date_info} | 进度: {scraped_count} 条已保存...[/bold cyan]")
                
                # 结束日期（在 ID 去重模式下，可直接使用 end_str，不再需要增加 1 天）
                search_end_str = end_str
                
                # 构造搜索 URL
                if scrape_target and not re.match(r'^\d{4}-\d{2}-\d{2}$', scrape_target):
                    post_id_input = str(scrape_target).strip()
                    target_bid = post_id_input
                    target_mid = post_id_input
                    ALPHABET = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
                    def base62_decode(s):
                        res = 0
                        for char in s:
                            res = res * 62 + ALPHABET.index(char)
                        return res

                    if post_id_input.isdigit():
                        mid = post_id_input
                        bid = ''
                        for i in range(len(mid) - 7, -7, -7):
                            offset = i if i > 0 else 0
                            length = 7 if i > 0 else len(mid) % 7
                            if length == 0 and offset == 0: length = 7
                            chunk = mid[offset:offset+length]
                            num = int(chunk)
                            b62 = ''
                            while num > 0:
                                b62 = ALPHABET[num % 62] + b62
                                num //= 62
                            if b62 == '': b62 = '0'
                            if offset > 0: bid = b62.zfill(4) + bid
                            else: bid = b62 + bid
                        target_bid = bid
                    else:
                        bid = post_id_input
                        mid = ''
                        for i in range(len(bid) - 4, -4, -4):
                            offset = i if i > 0 else 0
                            length = 4 if i > 0 else len(bid) % 4
                            if length == 0 and offset == 0: length = 4
                            chunk = bid[offset:offset+length]
                            num = base62_decode(chunk)
                            if offset > 0: mid = str(num).zfill(7) + mid
                            else: mid = str(num) + mid
                        target_mid = mid

                    import urllib.parse
                    url_encoded = urllib.parse.quote(f"https://weibo.com/{user_id}/{target_bid}", safe="")
                    search_url = f"https://s.weibo.com/weibo?q={url_encoded}"
                else:
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
                    except Exception:
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

                            # 1. 解析时间，通过 XPath 排除转发原博容器中的 p.from
                            from_el = card.locator("xpath=.//*[contains(@class, 'from') and not(ancestor::div[contains(@class, 'card-comment')])]").first
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
                                else:
                                    continue
                                
                            if not time_str:
                                continue

                            if start_str == "SINGLE_POST":
                                reference_dt = datetime.now()
                            else:
                                reference_dt = datetime.strptime(start_str, "%Y-%m-%d")
                            post_time = parse_weibo_time(time_str, reference_date=reference_dt)
                            if not post_time:
                                continue

                            # 提取发布设备 (device) 和发布位置 (ip_location)
                            device = ""
                            ip_location = ""

                            # 1. 尝试从新版 React 布局提取 (基于类名特征，且排除转发框)
                            ip_el = card.locator("xpath=.//div[contains(@class, '_ip_') and not(ancestor::div[contains(@class, 'card-comment')])]").first
                            if ip_el.is_visible():
                                ip_text = ip_el.get_attribute("title") or ip_el.inner_text()
                                if ip_text:
                                    device_raw = ip_text.replace("发布于", "").strip()
                                    if device_raw:
                                        ip_location = device_raw

                            source_el = card.locator("xpath=.//div[contains(@class, '_source_') and not(ancestor::div[contains(@class, 'card-comment')])]").first
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

                            if start_str == "SINGLE_POST" and target_mid and post_id != target_mid:
                                continue

                            if not post_link:
                                 if from_el.is_visible():
                                     link_el = from_el.locator("a").first
                                     if link_el.is_visible():
                                         post_link = link_el.get_attribute("href")

                            if post_link and post_link.startswith("//"):
                                post_link = "https:" + post_link
                            if post_link and "?" in post_link:
                                post_link = post_link.split("?")[0]
                            
                            if not post_link and post_id and post_id.isdigit():
                                post_link = f"https://weibo.com/{user_id}/{post_id}"
                            
                            if not post_id:
                                if post_link:
                                    post_id = post_link.split("/")[-1].split("?")[0]
                                else:
                                    post_id = str(post_time)

                            # --- 去重与时间过滤 ---
                            if post_id in processed_ids:
                                print(f"  ⏭️ 跳过本次已处理的重复微博: {post_id}")
                                continue
                                
                            if post_id in scraped_ids:
                                print(f"  ⏭️ 跳过历史已抓取的重复微博: {post_id}")
                                continue
                                
                            # 严格时间范围过滤 (单篇抓取时忽略时间过滤)
                            if start_str != "SINGLE_POST":
                                if user_info["start_time"] is not None:
                                    # 对于增量用户，如果该微博不在已抓取列表中，只要在回溯范围之内，我们都予以抓取以防漏掉
                                    if post_time < user_start_dt:
                                        continue
                                    if post_time > user_end_dt:
                                        continue
                                else:
                                    if not (user_start_dt <= post_time <= user_end_dt):
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
                                except Exception:
                                    pass
                            
                            # 4. 提取正文
                            content_full = card.locator("xpath=.//p[@node-type='feed_list_content_full' and not(ancestor::div[contains(@class, 'card-comment')])]").first
                            content_normal = card.locator("xpath=.//p[contains(@class, 'txt') and not(ancestor::div[contains(@class, 'card-comment')])]").first
                            
                            # JS 脚本，将所有带 alt 属性的图片（特别是表情）替换为其 alt 文本，防止表情丢失
                            js_preserve_emojis = """el => {
                                const clone = el.cloneNode(true);
                                clone.querySelectorAll('img[alt]').forEach(img => {
                                    if (img.alt && img.alt.startsWith('[') && img.alt.endsWith(']')) {
                                        img.replaceWith(document.createTextNode(img.alt));
                                    }
                                });
                                return clone.innerText;
                            }"""
                            
                            if content_full.is_visible():
                                content = content_full.evaluate(js_preserve_emojis).strip()
                            else:
                                content = content_normal.evaluate(js_preserve_emojis).strip()
                                
                            # 清理微博正文末尾多余的“收起d”、“展开c”等字符
                            content = re.sub(r'\s*(收起|展开)[a-zA-Z]$', '', content)
                                
                            # 5. 提取互动数据
                            footer = card.locator("div.card-act").first
                            stats_text = footer.inner_text().replace("\n", " ").strip() if footer.is_visible() else ""
                            
                            # 5.5 初始化媒体字段和原博属性字段
                            images = []
                            videos = []
                            download_videos = []
                            livephotos = []

                            retweet_user = ""
                            retweet_content = ""
                            retweet_images = []
                            retweet_videos = []
                            retweet_download_videos = []
                            retweet_livephotos = []
                            retweet_time = None
                            retweet_link = ""
                            retweet_id = ""
                            retweet_device = ""
                            retweet_ip_location = ""

                            # 5.5.1 如果是转发微博，提取原博的基本属性
                            if is_retweet:
                                retweet_box = card.locator("div.card-comment").first
                                if retweet_box.is_visible():
                                    retweet_user_el = retweet_box.locator("a[extra-data='type=atname']").first
                                    if retweet_user_el.is_visible():
                                        retweet_user = retweet_user_el.inner_text().strip()
                                    else:
                                        all_links = retweet_box.locator("a").all()
                                        for link in all_links:
                                            link_text = link.inner_text().strip()
                                            if link_text.startswith("@"):
                                                retweet_user = link_text
                                                break
                                                
                                    retweet_content_full = retweet_box.locator("p[node-type='feed_list_content_full']").first
                                    retweet_content_normal = retweet_box.locator("p.txt").first
                                    if retweet_content_full.is_visible():
                                        retweet_content = retweet_content_full.evaluate(js_preserve_emojis).strip()
                                    elif retweet_content_normal.is_visible():
                                        retweet_content = retweet_content_normal.evaluate(js_preserve_emojis).strip()
                                    else:
                                        all_txts = retweet_box.locator("p.txt").all()
                                        for txt_el in all_txts:
                                            if txt_el.is_visible():
                                                retweet_content = txt_el.evaluate(js_preserve_emojis).strip()
                                                break
                                    if not retweet_content:
                                        try:
                                            retweet_content = retweet_box.locator("p.txt").first.evaluate(js_preserve_emojis).strip()
                                        except Exception:
                                            pass
                                            
                                    # 清理转发微博正文末尾多余的“收起d”、“展开c”等字符
                                    retweet_content = re.sub(r'\s*(收起|展开)[a-zA-Z]$', '', retweet_content)
                                        
                                    retweet_from_el = retweet_box.locator(".from").first
                                    if retweet_from_el.is_visible():
                                        retweet_from_text = retweet_from_el.inner_text()
                                        retweet_time_link_el = retweet_from_el.locator("a").first
                                        if retweet_time_link_el.is_visible():
                                            retweet_time_raw = retweet_time_link_el.inner_text().strip()
                                            retweet_link = retweet_time_link_el.get_attribute("href") or ""
                                            if retweet_link.startswith("//"):
                                                retweet_link = "https:" + retweet_link
                                            if retweet_link and "?" in retweet_link:
                                                retweet_link = retweet_link.split("?")[0]
                                        else:
                                            date_match = re.search(r"(\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{1,2})", retweet_from_text)
                                            retweet_time_raw = date_match.group(1) if date_match else ""
                                            
                                        if retweet_time_raw:
                                            retweet_time = parse_weibo_time(retweet_time_raw, reference_date=post_time)
                                            
                                        if retweet_link:
                                            retweet_id = retweet_link.split("/")[-1].split("?")[0]
                                        if not retweet_id:
                                            retweet_id = retweet_box.get_attribute("mid") or ""
                                            
                                        ip_match = re.search(r"(?:发布于|IP属地[：:])\s*(\S+)", retweet_from_text)
                                        if ip_match:
                                            retweet_ip_location = ip_match.group(1).strip()
                                            
                                        source_match = re.search(r"来自\s*(.+)$", retweet_from_text)
                                        if source_match:
                                            device_raw = source_match.group(1).strip()
                                            if "发布于" in device_raw:
                                                device_raw = device_raw.split("发布于")[0].strip()
                                            elif "IP属地" in device_raw:
                                                device_raw = device_raw.split("IP属地")[0].strip()
                                            retweet_device = device_raw

                            # 5.5.2 提取图片链接 (原创从主卡，转发限制在原博框内)
                            temp_images = []
                            media_locator_base = card.locator("div.card-comment").first if is_retweet else card
                            should_save_images = (RETWEET_PIC_DOWNLOAD if is_retweet else ORIGINAL_PIC_DOWNLOAD)
                            if should_save_images and media_locator_base.is_visible():
                                try:
                                    img_locators = media_locator_base.locator("div.media-piclist img").all()
                                    for img_loc in img_locators:
                                        src = img_loc.get_attribute("src")
                                        if src:
                                            if src.startswith("//"):
                                                src = "https:" + src
                                            large_src = re.sub(r'/(thumb150|orj360|mw690|orj960|small|thumbnail)/', '/large/', src)
                                            temp_images.append(large_src)
                                except Exception as img_err:
                                    print(f"提取图片链接失败: {img_err}")
                                    
                            if is_retweet:
                                retweet_images = temp_images
                            else:
                                images = temp_images

                            # 5.5.3 提取视频链接 (原创从主卡，转发限制在原博框内)
                            temp_videos = []
                            temp_download_videos = []
                            should_save_videos = (RETWEET_VIDEO_DOWNLOAD if is_retweet else ORIGINAL_VIDEO_DOWNLOAD)
                            if should_save_videos and media_locator_base.is_visible():
                                try:
                                    video_el = media_locator_base.locator("a.WB_video_h5").first
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
                                        tech_video = media_locator_base.locator("video.wbpv-tech").first
                                        if tech_video.is_visible():
                                            video_url = tech_video.get_attribute("src")
                                            
                                    if video_url:
                                        if video_url.startswith("//"):
                                            video_url = "https:" + video_url
                                        import html
                                        video_url = html.unescape(video_url)
                                        
                                        stable_url = detail_url if detail_url else video_url
                                        temp_videos.append(stable_url)
                                        temp_download_videos.append(video_url)
                                except Exception as video_err:
                                    print(f"提取视频链接失败: {video_err}")
                                    
                            if is_retweet:
                                retweet_videos = temp_videos
                                retweet_download_videos = temp_download_videos
                            else:
                                videos = temp_videos
                                download_videos = temp_download_videos

                            # 5.5.4 提取 Live Photo 视频链接
                            temp_livephotos = []
                            should_save_livephotos = (RETWEET_LIVE_PHOTO_DOWNLOAD if is_retweet else ORIGINAL_LIVE_PHOTO_DOWNLOAD)
                            if should_save_livephotos and temp_images:
                                try:
                                    api_url = f"https://weibo.com/ajax/statuses/show?id={post_id}"
                                    api_res = page.context.request.get(api_url)
                                    if api_res.status == 200:
                                        post_detail = api_res.json()
                                        pic_infos = post_detail.get("pic_infos", {})
                                        for pic_id, pic_data in pic_infos.items():
                                            if pic_data.get("type") == "livephoto" and pic_data.get("video"):
                                                temp_livephotos.append(pic_data.get("video"))
                                except Exception as lp_err:
                                    print(f"提取实况照片视频失败 (ID: {post_id}): {lp_err}")
                                    
                            if is_retweet:
                                retweet_livephotos = temp_livephotos
                            else:
                                livephotos = temp_livephotos
                            print(f"抓取: {post_time} - {content[:20].strip().replace(chr(10), ' ')}...")
                            
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
                                "is_retweet": is_retweet,
                                "retweet_user": retweet_user,
                                "retweet_content": retweet_content,
                                "retweet_images": retweet_images,
                                "retweet_videos": retweet_videos,
                                "retweet_download_videos": retweet_download_videos,
                                "retweet_livephotos": retweet_livephotos,
                                "retweet_time": retweet_time,
                                "retweet_link": retweet_link,
                                "retweet_id": retweet_id,
                                "retweet_device": retweet_device,
                                "retweet_ip_location": retweet_ip_location
                            }
                            try:
                                global_dashboard.stop()
                                try:
                                    save_data([post_data], user_name, page)
                                finally:
                                    global_dashboard.start()
                                scraped_count += 1
                                date_info = f"指定微博 ID: {scrape_target}" if start_str == "SINGLE_POST" else (f"日期: {start_str}" if start_str == end_str else f"日期: {start_str} 至 {end_str}")
                                global_dashboard.update(f"[bold cyan]正在抓取第 {user_idx}/{len(user_ids)} 个用户: {user_name} ({user_id}) | {date_info} | 进度: {scraped_count} 条已保存...[/bold cyan]")
                                scraped_ids.add(post_id)
                                processed_ids.add(post_id)
                            except Exception as save_err:
                                print(f"  -> ⚠️ 保存微博 {post_id} 失败: {save_err}")
                            
                        except Exception as e:
                            print(f"解析出错: {e}")
                            continue

                    if start_str == "SINGLE_POST":
                        break
                    
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
                
                day_saved = scraped_count - initial_scraped_count
                if start_str != "SINGLE_POST":
                    global_dashboard.stop()
                    global_console.print(f"  [green]✅ {start_str} 共保存了 {day_saved} 条新微博[/green]")
                    global_dashboard.start()
                
                time.sleep(3)

            original_print(f"\n✅ 用户 {user_name} 抓取完毕，本次共新抓取并保存了 {scraped_count} 条微博。")

            # 该用户完全抓取成功后，更新对应文件的增量时间戳 (单篇/单日指定时不要更新)
            if TARGET_USER_IDS.endswith(".txt") and not scrape_target:
                crawl_end_time = datetime.now().replace(microsecond=0)
                crawl_end_time_str = crawl_end_time.strftime("%Y-%m-%dT%H:%M:%S")
                update_userid_file(TARGET_USER_IDS, user_id, user_name, crawl_end_time_str)

        try:
            headless_browser.close()
        except Exception:
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

    # 使用强正则按 \n## HH:MM:SS 分割各条微博，防止被用户正文中的 `## ` 截断
    parts = re.split(r'\n## (?=\d{2}:\d{2}:\d{2})', content)
    if len(parts) <= 1:
        # 如果第一条就在文件头部，可能没有前导的 \n
        parts = re.split(r'^## (?=\d{2}:\d{2}:\d{2})', content, flags=re.MULTILINE)
        if len(parts) <= 1:
            return []

    posts = []
    start_idx = 1
    # 如果用 MULTILINE 切分，或者文件开头恰好满足，第一部分可能是空字符串或者无关的头信息
    # 只要 parts[0] 是空字符串且有后续部分，我们也可以跳过 parts[0]
    if parts[0].strip() == "":
        start_idx = 1
    # 原本的兼容性处理，以防意外
    elif parts[0].startswith("## "):
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
    csv_dir = target_dir if target_dir else os.path.join(OUTPUT_DIR, safe_filename(user_name))
    os.makedirs(csv_dir, exist_ok=True)
    csv_name = f"posts_{suffix}.csv" if suffix else "posts.csv"
    csv_path = os.path.join(csv_dir, csv_name)
    
    df_data = []
    for post in data:
        rt_time_val = post.get("retweet_time")
        rt_time_str = rt_time_val.strftime("%Y-%m-%d %H:%M:%S") if isinstance(rt_time_val, datetime) else str(rt_time_val or "")
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
            "ip_location": post.get("ip_location", ""),
            "retweet_user": post.get("retweet_user", ""),
            "retweet_content": post.get("retweet_content", ""),
            "retweet_images": ",".join(post.get("retweet_images", [])),
            "retweet_videos": ",".join(post.get("retweet_videos", [])),
            "retweet_livephotos": ",".join(post.get("retweet_livephotos", [])),
            "retweet_time": rt_time_str,
            "retweet_link": post.get("retweet_link", ""),
            "retweet_id": post.get("retweet_id", ""),
            "retweet_device": post.get("retweet_device", ""),
            "retweet_ip_location": post.get("retweet_ip_location", "")
        })
        
    df_new = pd.DataFrame(df_data)
    
    if os.path.exists(csv_path):
        try:
            # 采用 O(1) 追加写入，不再全量读写合并，极大提升写入性能
            df_new.to_csv(csv_path, mode='a', header=False, index=False, encoding="utf-8-sig")
        except Exception as e:
            print(f"  -> ⚠️ 追加写入 CSV 归档失败: {e}，将尝试重写...")
            df_new.to_csv(csv_path, index=False, encoding="utf-8-sig")
    else:
        df_new.to_csv(csv_path, index=False, encoding="utf-8-sig")
        
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
                    df_comments_new.to_csv(comments_csv_path, mode='a', header=False, index=False, encoding="utf-8-sig")
                except Exception as e:
                    print(f"  -> ⚠️ 追加写入评论 CSV 失败: {e}，将尝试重写...")
                    df_comments_new.to_csv(comments_csv_path, index=False, encoding="utf-8-sig")
            else:
                df_comments_new.to_csv(comments_csv_path, index=False, encoding="utf-8-sig")
                # print("Comments CSV written.")


def save_to_sqlite(data, user_name, target_dir=None, suffix=""):
    """
    将用户的抓取结果写入 weibo/用户名/posts.db 本地 SQLite 数据库中。
    """
    if not data:
        return
    import sqlite3
    import json
    
    db_dir = target_dir if target_dir else os.path.join(OUTPUT_DIR, safe_filename(user_name))
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
            ip_location TEXT,
            retweet_user TEXT,
            retweet_content TEXT,
            retweet_images TEXT,
            retweet_videos TEXT,
            retweet_livephotos TEXT,
            retweet_time TEXT,
            retweet_link TEXT,
            retweet_id TEXT,
            retweet_device TEXT,
            retweet_ip_location TEXT
        )
    """)
    conn.commit()
    
    # 动态扩容以防止已有数据库报错
    cursor.execute("PRAGMA table_info(posts)")
    existing_posts_cols = {row[1] for row in cursor.fetchall()}
    for col in [
        "device", "ip_location", "retweet_user", "retweet_content", "retweet_images",
        "retweet_videos", "retweet_livephotos", "retweet_time", "retweet_link",
        "retweet_id", "retweet_device", "retweet_ip_location"
    ]:
        if col not in existing_posts_cols:
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
        cursor.execute("PRAGMA table_info(comments)")
        existing_comments_cols = {row[1] for row in cursor.fetchall()}
        for col in ["parent_id", "media_url", "post_time", "post_summary", "source"]:
            if col not in existing_comments_cols:
                try:
                    cursor.execute(f"ALTER TABLE comments ADD COLUMN {col} TEXT")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
    
    for post in data:
        post_time_val = post.get("time")
        post_time_str = post_time_val.strftime("%Y-%m-%d %H:%M:%S") if isinstance(post_time_val, datetime) else str(post_time_val)
        post_content = post.get("content", "")
        post_summary = post_content[:20].replace("\n", " ").strip() + ("..." if len(post_content) > 20 else "")

        rt_time_val = post.get("retweet_time")
        rt_time_str = rt_time_val.strftime("%Y-%m-%d %H:%M:%S") if isinstance(rt_time_val, datetime) else str(rt_time_val or "")
        cursor.execute("""
            INSERT OR REPLACE INTO posts (
                id, time, link, content, reposts_count, comments_count, attitudes_count, images, videos, livephotos, device, ip_location,
                retweet_user, retweet_content, retweet_images, retweet_videos, retweet_livephotos, retweet_time, retweet_link, retweet_id, retweet_device, retweet_ip_location
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            post.get("ip_location", ""),
            post.get("retweet_user", ""),
            post.get("retweet_content", ""),
            json.dumps(post.get("retweet_images", [])),
            json.dumps(post.get("retweet_videos", [])),
            json.dumps(post.get("retweet_livephotos", [])),
            rt_time_str,
            post.get("retweet_link", ""),
            post.get("retweet_id", ""),
            post.get("retweet_device", ""),
            post.get("retweet_ip_location", "")
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
    # print(f"SQLite synced: {db_path}")


def save_to_json(data, user_name, target_dir=None, suffix=""):
    """
    将用户的抓取结果保存到 JSON 文件中。
    """
    if not data:
        return
    import json
    
    json_dir = target_dir if target_dir else os.path.join(OUTPUT_DIR, safe_filename(user_name))
    os.makedirs(json_dir, exist_ok=True)
    json_name = f"posts_{suffix}.json" if suffix else "posts.json"
    json_path = os.path.join(json_dir, json_name)
    
    new_json_data = []
    for post in data:
        post_time_str = post.get("time").strftime("%Y-%m-%d %H:%M:%S") if isinstance(post.get("time"), datetime) else str(post.get("time"))
        
        rt_time_val = post.get("retweet_time")
        rt_time_str = rt_time_val.strftime("%Y-%m-%d %H:%M:%S") if isinstance(rt_time_val, datetime) else str(rt_time_val or "")
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
            "ip_location": post.get("ip_location", ""),
            "retweet_user": post.get("retweet_user", ""),
            "retweet_content": post.get("retweet_content", ""),
            "retweet_images": post.get("retweet_images", []),
            "retweet_videos": post.get("retweet_videos", []),
            "retweet_livephotos": post.get("retweet_livephotos", []),
            "retweet_time": rt_time_str,
            "retweet_link": post.get("retweet_link", ""),
            "retweet_id": post.get("retweet_id", ""),
            "retweet_device": post.get("retweet_device", ""),
            "retweet_ip_location": post.get("retweet_ip_location", "")
        }
        if "comments" in post:
            post_item["comments"] = post["comments"]
            
        new_json_data.append(post_item)
        
    # 采用 O(1) 尾部追加逻辑，避免随着数据量增长导致重复全量读写的 I/O 灾难
    new_json_data.sort(key=lambda x: x.get("time", ""))
    
    if not os.path.exists(json_path):
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(new_json_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  -> ❌ 保存 JSON 失败: {e}")
        return

    try:
        with open(json_path, "r+", encoding="utf-8") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            found = False
            while pos > 0:
                pos -= 1
                f.seek(pos, os.SEEK_SET)
                if f.read(1) == ']':
                    found = True
                    break
            
            if found:
                f.seek(0)
                file_content = f.read(pos).strip()
                is_empty = file_content.endswith('[')
                
                f.seek(pos, os.SEEK_SET)
                new_json_str = json.dumps(new_json_data, ensure_ascii=False, indent=2)
                new_json_str = new_json_str.strip()
                if new_json_str.startswith('['):
                    new_json_str = new_json_str[1:]
                if new_json_str.endswith(']'):
                    new_json_str = new_json_str[:-1]
                new_json_str = new_json_str.strip()
                
                if new_json_str:
                    if not is_empty:
                        f.write(',\n  ')
                    else:
                        f.write('\n  ')
                    f.write(new_json_str)
                    f.write('\n]')
                f.truncate()
            else:
                # 兼容处理：文件损坏或非数组格式时，退化为全量覆盖
                f.seek(0)
                try:
                    old_data = json.load(f)
                except:
                    old_data = []
                if isinstance(old_data, list):
                    old_data.extend(new_json_data)
                f.seek(0)
                f.truncate()
                json.dump(old_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  -> ⚠️ O(1) 追加 JSON 失败: {e}")


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
        month_dir = os.path.join(OUTPUT_DIR, safe_filename(user_name), month_str)
        
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
            
            # 动态选择媒体 URLs
            is_retweet = post.get("is_retweet", False)
            if is_retweet:
                media_images = post.get("retweet_images", [])
                media_videos = post.get("retweet_download_videos") if post.get("retweet_download_videos") is not None else post.get("retweet_videos", [])
                media_livephotos = post.get("retweet_livephotos", [])
                
                img_dir_path = img_dir
                video_dir_path = video_dir
                livephoto_dir_path = livephoto_dir
                
                img_relative = img_rel
                video_relative = video_rel
                livephoto_relative = livephoto_rel
            else:
                media_images = post.get("images", [])
                media_videos = post.get("download_videos") if post.get("download_videos") is not None else post.get("videos", [])
                media_livephotos = post.get("livephotos", [])
                
                img_dir_path = img_dir
                video_dir_path = video_dir
                livephoto_dir_path = livephoto_dir
                
                img_relative = img_rel
                video_relative = video_rel
                livephoto_relative = livephoto_rel

            # 保存图片逻辑
            local_image_paths = []
            if ENABLE_SAVE_IMAGES and media_images:
                os.makedirs(img_dir_path, exist_ok=True)
                local_image_paths = [None] * len(media_images)
                tasks = []
                for idx, img_url in enumerate(media_images):
                    ext = "jpg"
                    ext_match = re.search(r'\.(\w+)(?:\?|$)', img_url)
                    if ext_match:
                        ext = ext_match.group(1)
                    
                    img_name = f"{time_prefix}_{safe_post_id}_{idx + 1}.{ext}"
                    img_save_path = os.path.join(img_dir_path, img_name)
                    local_path = f"{img_relative}/{img_name}"
                    
                    if not os.path.exists(img_save_path):
                        tasks.append({
                            "idx": idx,
                            "url": img_url,
                            "save_path": img_save_path,
                            "local_path": local_path,
                            "desc": f"图片 {idx + 1}/{len(media_images)}"
                        })
                    else:
                        local_image_paths[idx] = local_path
                
                if tasks:
                    from concurrent.futures import ThreadPoolExecutor
                    from rich.progress import Progress, SpinnerColumn, TextColumn
                    max_workers = DOWNLOAD_NUM_CONCURRENT_MEDIA
                    
                    with Progress(
                        SpinnerColumn(spinner_name="dots"),
                        TextColumn("[progress.description]{task.description} ({task.completed}/{task.total})"),
                        transient=True
                    ) as progress:
                        task_id = progress.add_task("", visible=False, total=len(tasks))
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            def worker(task):
                                success = download_file(task["url"], task["save_path"], page, show_progress=False)
                                return task, success
                            
                            for task, success in executor.map(worker, tasks):
                                if success:
                                    local_image_paths[task["idx"]] = task["local_path"]
                                progress.advance(task_id)
                                
                    pass
                    sys.stdout.flush()
                                
                local_image_paths = [p for p in local_image_paths if p is not None]
                        
            # 保存视频逻辑
            local_video_paths = []
            if ENABLE_SAVE_VIDEOS and media_videos:
                os.makedirs(video_dir_path, exist_ok=True)
                local_video_paths = [None] * len(media_videos)
                tasks = []
                for idx, video_url in enumerate(media_videos):
                    if "video.weibo.com" in video_url or "weibo.com/tv" in video_url:
                        continue
                    ext = "mp4"
                    ext_match = re.search(r'\.(\w+)(?:\?|$)', video_url)
                    if ext_match:
                        ext = ext_match.group(1)
                    
                    video_name = f"{time_prefix}_{safe_post_id}_{idx + 1}.{ext}"
                    video_save_path = os.path.join(video_dir_path, video_name)
                    local_path = f"{video_relative}/{video_name}"
                    
                    if not os.path.exists(video_save_path):
                        tasks.append({
                            "idx": idx,
                            "url": video_url,
                            "save_path": video_save_path,
                            "local_path": local_path,
                            "desc": f"视频 {idx + 1}/{len(media_videos)}"
                        })
                    else:
                        local_video_paths[idx] = local_path
                
                if tasks:
                    from concurrent.futures import ThreadPoolExecutor
                    from rich.progress import Progress, SpinnerColumn, TextColumn
                    max_workers = DOWNLOAD_NUM_CONCURRENT_MEDIA
                    
                    with Progress(
                        SpinnerColumn(spinner_name="dots"),
                        TextColumn("[progress.description]{task.description} ({task.completed}/{task.total})"),
                        transient=True
                    ) as progress:
                        task_id = progress.add_task("", visible=False, total=len(tasks))
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            def worker(task):
                                success = download_file(task["url"], task["save_path"], page, show_progress=False)
                                return task, success
                            
                            for task, success in executor.map(worker, tasks):
                                if success:
                                    local_video_paths[task["idx"]] = task["local_path"]
                                progress.advance(task_id)
                                
                    pass
                    sys.stdout.flush()
                                
                local_video_paths = [p for p in local_video_paths if p is not None]
                        
            # 保存实况照片 (Live Photo) 逻辑
            local_livephoto_paths = []
            if ENABLE_SAVE_LIVEPHOTOS and media_livephotos:
                os.makedirs(livephoto_dir_path, exist_ok=True)
                local_livephoto_paths = [None] * len(media_livephotos)
                tasks = []
                for idx, lp_url in enumerate(media_livephotos):
                    ext = "mov"
                    ext_match = re.search(r'\.(\w+)(?:\?|$)', lp_url)
                    if ext_match:
                        ext = ext_match.group(1)
                    
                    lp_name = f"{time_prefix}_{safe_post_id}_{idx + 1}.{ext}"
                    lp_save_path = os.path.join(livephoto_dir_path, lp_name)
                    local_path = f"{livephoto_relative}/{lp_name}"
                    
                    if not os.path.exists(lp_save_path):
                        tasks.append({
                            "idx": idx,
                            "url": lp_url,
                            "save_path": lp_save_path,
                            "local_path": local_path,
                            "desc": f"实况视频 {idx + 1}/{len(media_livephotos)}"
                        })
                    else:
                        local_livephoto_paths[idx] = local_path
                        
                if tasks:
                    from concurrent.futures import ThreadPoolExecutor
                    from rich.progress import Progress, SpinnerColumn, TextColumn
                    max_workers = DOWNLOAD_NUM_CONCURRENT_MEDIA
                    
                    with Progress(
                        SpinnerColumn(spinner_name="dots"),
                        TextColumn("[progress.description]{task.description} ({task.completed}/{task.total})"),
                        transient=True
                    ) as progress:
                        task_id = progress.add_task("", visible=False, total=len(tasks))
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            def worker(task):
                                success = download_file(task["url"], task["save_path"], page, show_progress=False)
                                return task, success
                            
                            for task, success in executor.map(worker, tasks):
                                if success:
                                    local_livephoto_paths[task["idx"]] = task["local_path"]
                                progress.advance(task_id)
                                
                    pass
                    sys.stdout.flush()
                                
                local_livephoto_paths = [p for p in local_livephoto_paths if p is not None]

            # 组装图片 Markdown 引用
            md_images = []
            if local_image_paths:
                md_images.append("")
                for lp in local_image_paths:
                    md_images.append(f"![image]({lp})")
                md_images.append("")

            # 组装视频 Markdown 引用
            md_videos = []
            if local_video_paths:
                md_videos.append("")
                for lp in local_video_paths:
                    md_videos.append(f'<video src="{lp}" controls width="100%"></video>')
                md_videos.append("")

            # 组装实况 Markdown 引用
            md_livephotos = []
            if local_livephoto_paths:
                md_livephotos.append("\n*实况照片动效视频:*")
                for lp in local_livephoto_paths:
                    md_livephotos.append(f'<video src="{lp}" controls width="100%"></video>')
                md_livephotos.append("")

            # 写入 Markdown 文本
            if not is_retweet:
                # 原创微博：直接追加到主文本
                body_lines.extend(md_images)
                body_lines.extend(md_videos)
                body_lines.extend(md_livephotos)
            else:
                # 转发微博：以引用框格式追加原博的全部数据
                retweet_lines = []
                rt_user = post.get("retweet_user", "")
                rt_content = post.get("retweet_content", "")
                
                # 去除前缀昵称
                if rt_user and rt_content:
                    prefix1 = rt_user
                    prefix2 = f"@{rt_user}" if not rt_user.startswith("@") else rt_user
                    for prefix in (prefix2, prefix1):
                        if rt_content.startswith(prefix):
                            rt_content = rt_content.replace(prefix, "", 1).strip().lstrip(" :：\n")
                            break
                            
                rt_user_display = rt_user if rt_user.startswith("@") else f"@{rt_user}"
                retweet_lines.append(rt_user_display)
                retweet_lines.append("")
                retweet_lines.append(rt_content)
                
                # 追加原博的多媒体
                retweet_lines.extend(md_images)
                retweet_lines.extend(md_videos)
                retweet_lines.extend(md_livephotos)
                
                # 组装原博的发布时间和设备来源
                rt_time_val = post.get("retweet_time")
                if rt_time_val:
                    rt_time_str = rt_time_val.strftime("%m月%d日 %H:%M") if isinstance(rt_time_val, datetime) else str(rt_time_val)
                else:
                    rt_time_str = ""
                    
                rt_device = post.get("retweet_device", "")
                rt_info_parts = []
                if rt_time_str:
                    rt_info_parts.append(rt_time_str)
                if rt_device:
                    rt_info_parts.append(f"来自 {rt_device}")
                    
                rt_info_str = " ".join(rt_info_parts)
                if rt_info_str:
                    retweet_lines.append("")
                    retweet_lines.append(rt_info_str)
                    
                # 以 blockquote ("> ") 格式追加到主体中，先展开多行文本以保证每一行都带有 "> " 前缀
                body_lines.append("")
                expanded_retweet_lines = []
                for rtl in retweet_lines:
                    if isinstance(rtl, str):
                        expanded_retweet_lines.extend(rtl.split("\n"))
                    else:
                        expanded_retweet_lines.append(rtl)
                
                for rtl in expanded_retweet_lines:
                    if rtl.strip() == "":
                        body_lines.append(">")
                    else:
                        body_lines.append(f"> {rtl}")
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
                    from rich.progress import Progress, SpinnerColumn, TextColumn
                    max_workers = DOWNLOAD_NUM_CONCURRENT_MEDIA
                    
                    with Progress(
                        SpinnerColumn(spinner_name="dots"),
                        TextColumn("[progress.description]{task.description} ({task.completed}/{task.total})"),
                        transient=True
                    ) as progress:
                        task_id = progress.add_task("", visible=False, total=len(comment_tasks))
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            def worker(task):
                                success = download_file(task["url"], task["save_path"], page, show_progress=False)
                                return task, success
                            
                            for task, success in executor.map(worker, comment_tasks):
                                if success:
                                    media_path_map[task["id"]] = task["local_path"]
                                progress.advance(task_id)
                                
                    pass
                    sys.stdout.flush()
                                
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
            
    user_dir = os.path.join(OUTPUT_DIR, safe_filename(user_name))
    if len(data) == 1:
        post_id = data[0].get("id", "未知ID")
        print(f"  -> 微博 {post_id} 数据已成功保存")
    else:
        print(f"  -> 成功同步 {len(data)} 条数据至 {user_dir}")
    
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
            month_dir = os.path.join(OUTPUT_DIR, safe_filename(user_name), month_str)
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
            

def delete_local_post_data(post_id, target_uid=None):
    """
    遍历本地存储目录，删除指定 post_id 对应的微博记录及相关评论
    支持传入数字 ID 或 base62 bid，自动双向转换以保证删除彻底
    """
    import os
    import json
    import sqlite3
    import pandas as pd
    
    post_id = str(post_id).strip()
    target_id = post_id
    target_bid = post_id
    
    ALPHABET = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
    def base62_decode(s):
        res = 0
        for char in s:
            res = res * 62 + ALPHABET.index(char)
        return res
    
    try:
        if post_id.isdigit():
            # 输入是数字 mid，转换为 bid
            mid = post_id
            bid = ''
            for i in range(len(mid) - 7, -7, -7):
                offset = i if i > 0 else 0
                length = 7 if i > 0 else len(mid) % 7
                if length == 0 and offset == 0: length = 7
                chunk = mid[offset:offset+length]
                num = int(chunk)
                b62 = ''
                while num > 0:
                    b62 = ALPHABET[num % 62] + b62
                    num //= 62
                if b62 == '': b62 = '0'
                if offset > 0: bid = b62.zfill(4) + bid
                else: bid = b62 + bid
            target_bid = bid
        else:
            # 输入是字母数字 bid，转换为 mid
            bid = post_id
            mid = ''
            for i in range(len(bid) - 4, -4, -4):
                offset = i if i > 0 else 0
                length = 4 if i > 0 else len(bid) % 4
                if length == 0 and offset == 0: length = 4
                chunk = bid[offset:offset+length]
                num = base62_decode(chunk)
                if offset > 0: mid = str(num).zfill(7) + mid
                else: mid = str(num) + mid
            target_id = mid
    except Exception as e:
        print(f"⚠️ ID 转换失败: {e}，将仅使用原输入进行精确匹配。")
    
    print(f"\n正在扫描并删除本地数据中 ID={target_id} (BID={target_bid}) 的所有记录...")
    
    base_dir = OUTPUT_DIR
    if not os.path.exists(base_dir):
        print(f"目录 {base_dir} 不存在。")
        return
        
    user_dirs = []
    if target_uid:
        for d in os.listdir(base_dir):
            d_path = os.path.join(base_dir, d)
            if os.path.isdir(d_path) and os.path.exists(os.path.join(d_path, f"{target_uid}.txt")):
                user_dirs.append(d_path)
        if not user_dirs:
            print(f"未找到对应 UID {target_uid} 的本地数据目录。")
            return
    else:
        user_dirs = [os.path.join(base_dir, d) for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
        
    deleted_count = {"csv": 0, "json": 0, "sqlite": 0, "md": 0}
    
    for ud in user_dirs:
        # 优化：精准投递，避免遍历 img/video 等媒体子目录
        target_files = [
            os.path.join(ud, 'posts.json'),
            os.path.join(ud, 'posts.csv'),
            os.path.join(ud, 'posts.db'),
            os.path.join(ud, 'comments.json'),
            os.path.join(ud, 'comments.csv'),
            os.path.join(ud, 'comments.db')
        ]
        # 收集所有的 markdown 存档文件
        try:
            for item in os.listdir(ud):
                if re.match(r'^\d{4}-\d{2}$', item):
                    month_dir = os.path.join(ud, item)
                    if os.path.isdir(month_dir):
                        target_files.extend([
                            os.path.join(month_dir, f"posts_{item}.json"),
                            os.path.join(month_dir, f"posts_{item}.csv"),
                            os.path.join(month_dir, f"posts_{item}.db"),
                            os.path.join(month_dir, f"comments_{item}.json"),
                            os.path.join(month_dir, f"comments_{item}.csv"),
                            os.path.join(month_dir, f"comments_{item}.db")
                        ])
                        for md_file in os.listdir(month_dir):
                            if md_file.endswith('.md') or md_file.endswith('.txt'):
                                target_files.append(os.path.join(month_dir, md_file))
        except Exception as e:
            print("COLLECT ERROR:", e)
        for file_path in target_files:
            print("Target file:", file_path)
            if not os.path.exists(file_path):
                continue

            
            # 快速预过滤，极大提升删除速度
            if file_path.endswith(('.json', '.csv', '.md', '.txt')):
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    if target_id not in content and target_bid not in content:
                            continue
                except Exception:
                    pass
            
            # 处理 JSON 文件
            if file_path.endswith('.json'):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                
                    if isinstance(data, list):
                        original_len = len(data)
                        new_data = [item for item in data if str(item.get('id', '')) not in (target_id, target_bid) and str(item.get('bid', '')) not in (target_id, target_bid)]
                        if len(new_data) < original_len:
                            with open(file_path, 'w', encoding='utf-8') as f:
                                json.dump(new_data, f, ensure_ascii=False, indent=2)
                            deleted_count["json"] += (original_len - len(new_data))
                            print(f"  [JSON] 已从 {file_path} 中删除 {(original_len - len(new_data))} 条记录")
                except Exception as e:
                    print(f"  读取/修改 JSON 出错: {file_path}, 错误: {e}")
                
            # 处理 CSV 文件
            elif file_path.endswith('.csv'):
                try:
                    df = pd.read_csv(file_path, dtype=str)
                    original_len = len(df)
                
                    # 宽松匹配，只要行内任何一列包含 target_id 或 target_bid，就干掉
                    mask = pd.Series([False] * len(df), index=df.index)
                    for col in df.columns:
                        col_str = df[col].astype(str)
                        mask = mask | col_str.str.contains(target_id, regex=False) | col_str.str.contains(target_bid, regex=False)
                
                    new_df = df[~mask]
                    if len(new_df) < original_len:
                        new_df.to_csv(file_path, index=False, encoding='utf-8-sig')
                        deleted_count["csv"] += (original_len - len(new_df))
                        print(f"  [CSV] 已从 {file_path} 中删除 {(original_len - len(new_df))} 条记录")
                except Exception as e:
                    pass
                
            # 处理 SQLite 文件
            elif file_path.endswith('.db'):
                try:
                    conn = sqlite3.connect(file_path)
                    cursor = conn.cursor()
                
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [row[0] for row in cursor.fetchall()]
                
                    if 'tweets' in tables:
                        cursor.execute("PRAGMA table_info(tweets)")
                        columns = [c[1] for c in cursor.fetchall()]
                        if 'bid' in columns:
                            cursor.execute("DELETE FROM tweets WHERE id IN (?, ?) OR bid IN (?, ?)", (target_id, target_bid, target_id, target_bid))
                        else:
                            cursor.execute("DELETE FROM tweets WHERE id IN (?, ?)", (target_id, target_bid))
                        if cursor.rowcount > 0:
                            deleted_count["sqlite"] += cursor.rowcount
                            print(f"  [SQLite] 从 {file_path} 的 tweets 表中删除 {cursor.rowcount} 条记录")
                        
                    if 'comments' in tables:
                        cursor.execute("DELETE FROM comments WHERE post_id=? OR post_id=?", (target_id, target_bid))
                        if cursor.rowcount > 0:
                            deleted_count["sqlite"] += cursor.rowcount
                            print(f"  [SQLite] 从 {file_path} 的 comments 表中删除 {cursor.rowcount} 条记录")
                        
                    if 'replies' in tables:
                        cursor.execute("DELETE FROM replies WHERE post_id=? OR post_id=?", (target_id, target_bid))
                        if cursor.rowcount > 0:
                            deleted_count["sqlite"] += cursor.rowcount
                            print(f"  [SQLite] 从 {file_path} 的 replies 表中删除 {cursor.rowcount} 条记录")
                        
                    conn.commit()
                    conn.close()
                except Exception as e:
                    print(f"  读取/修改 SQLite 出错: {file_path}, 错误: {e}")
                
            # 处理 Markdown 文件
            elif file_path.endswith('.md') or file_path.endswith('.txt'):
                try:
                    posts = parse_markdown_posts(file_path)
                    original_len = len(posts)
                    if original_len > 0:
                        new_posts = []
                        for p in posts:
                            pid = str(p.get("id", ""))
                            body_content = p.get("body", "")
                            # 宽松匹配
                            if target_id in pid or target_bid in pid or target_id in body_content or target_bid in body_content:
                                pass
                            else:
                                new_posts.append(p)
                            
                        if len(new_posts) < original_len:
                            if len(new_posts) == 0:
                                import os
                                os.remove(file_path)
                                deleted_count["md"] += original_len
                                print(f"  [Markdown] 已删除文件 {file_path}")
                            else:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    first_line = f.readline()
                                
                                with open(file_path, "w", encoding="utf-8") as f:
                                    if first_line.startswith("# "):
                                        f.write(first_line.strip() + "\n\n")
                                    else:
                                        f.write("# 微博存档\n\n")
                                    
                                    for p in new_posts:
                                        f.write(f"## {p['time_str']}\n\n")
                                        f.write(f"{p['body']}\n\n")
                                        f.write(f"---\n\n")
                                deleted_count["md"] += (original_len - len(new_posts))
                                print(f"  [Markdown] 已从 {file_path} 中删除 {(original_len - len(new_posts))} 条记录")
                except Exception:
                    pass

    print("\n--- 清理完成 ---")
    print(f"共删除 JSON 记录: {deleted_count['json']} 条")
    print(f"共删除 CSV 记录: {deleted_count['csv']} 条")
    print(f"共删除 SQLite 记录: {deleted_count['sqlite']} 条")
    print(f"共删除 Markdown 记录: {deleted_count['md']} 条")

def delete_local_data_by_date(target_date, target_uid=None):
    """
    遍历本地存储目录，删除指定日期的微博记录及相关评论
    target_date 格式应为 YYYY-MM-DD
    """
    import os
    import json
    import sqlite3
    import pandas as pd
    
    print(f"\n正在扫描并收集本地数据中日期为 {target_date} 的相关 ID...")
    
    base_dir = OUTPUT_DIR
    if not os.path.exists(base_dir):
        print(f"目录 {base_dir} 不存在。")
        return
        
    user_dirs = []
    if target_uid:
        for d in os.listdir(base_dir):
            d_path = os.path.join(base_dir, d)
            if os.path.isdir(d_path) and os.path.exists(os.path.join(d_path, f"{target_uid}.txt")):
                user_dirs.append(d_path)
        if not user_dirs:
            print(f"未找到对应 UID {target_uid} 的本地数据目录。")
            return
    else:
        user_dirs = [os.path.join(base_dir, d) for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
        
    deleted_count = {"csv": 0, "json": 0, "sqlite": 0, "md": 0}
    deleted_post_ids = set()
    
    # 第一遍：收集该日期的所有 post_id 和 bid
    # 优化：采用精确定位，仅读取核心数据文件，避免海量媒体文件的遍历开销
    for ud in user_dirs:
        primary_files = [
            os.path.join(ud, 'posts.json'),
            os.path.join(ud, 'posts.csv'),
            os.path.join(ud, 'posts.db')
        ]
        try:
            for item in os.listdir(ud):
                if re.match(r'^\d{4}-\d{2}$', item):
                    month_dir = os.path.join(ud, item)
                    if os.path.isdir(month_dir):
                        primary_files.extend([
                            os.path.join(month_dir, f"posts_{item}.json"),
                            os.path.join(month_dir, f"posts_{item}.csv"),
                            os.path.join(month_dir, f"posts_{item}.db")
                        ])
        except Exception: pass
        for file_path in primary_files:
            if not os.path.exists(file_path):
                continue

            
            # 快速预过滤
            if file_path.endswith(('.json', '.csv', '.md', '.txt')):
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        if target_date not in f.read():
                            continue
                except Exception:
                    pass
        
            if file_path.endswith('.json'):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            if str(item.get('time', '')).startswith(target_date):
                                if 'id' in item: deleted_post_ids.add(str(item['id']))
                                if 'bid' in item: deleted_post_ids.add(str(item['bid']))
                except Exception: pass
            elif file_path.endswith('.csv'):
                try:
                    df = pd.read_csv(file_path, dtype=str)
                    if 'time' in df.columns:
                        mask = df['time'].astype(str).str.startswith(target_date)
                        for _, row in df[mask].iterrows():
                            if 'id' in row and pd.notna(row['id']): deleted_post_ids.add(str(row['id']))
                            if 'bid' in row and pd.notna(row['bid']): deleted_post_ids.add(str(row['bid']))
                except Exception: pass
            elif file_path.endswith('.db'):
                try:
                    conn = sqlite3.connect(file_path)
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [row[0] for row in cursor.fetchall()]
                    if 'tweets' in tables:
                        cursor.execute("SELECT id, bid FROM tweets WHERE time LIKE ?", (f"{target_date}%",))
                        for row in cursor.fetchall():
                            if row[0]: deleted_post_ids.add(str(row[0]))
                            if len(row) > 1 and row[1]: deleted_post_ids.add(str(row[1]))
                    conn.close()
                except Exception: pass

    print(f"找到 {len(deleted_post_ids)} 个相关微博 ID，开始清理...")
    
    for ud in user_dirs:
        month_str = target_date[:7]
        target_files = [
            os.path.join(ud, 'posts.json'),
            os.path.join(ud, 'posts.csv'),
            os.path.join(ud, 'posts.db'),
            os.path.join(ud, 'comments.json'),
            os.path.join(ud, 'comments.csv'),
            os.path.join(ud, 'comments.db'),
            os.path.join(ud, month_str, f"{target_date}.md")
        ]
        month_dir = os.path.join(ud, month_str)
        if os.path.exists(month_dir) and os.path.isdir(month_dir):
            target_files.extend([
                os.path.join(month_dir, f"posts_{month_str}.json"),
                os.path.join(month_dir, f"posts_{month_str}.csv"),
                os.path.join(month_dir, f"posts_{month_str}.db"),
                os.path.join(month_dir, f"comments_{month_str}.json"),
                os.path.join(month_dir, f"comments_{month_str}.csv"),
                os.path.join(month_dir, f"comments_{month_str}.db")
            ])
        for file_path in target_files:
            if not os.path.exists(file_path):
                continue

            
            # 快速预过滤
            if file_path.endswith(('.json', '.csv', '.md', '.txt')):
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        if target_date not in content and not any(d_id in content for d_id in deleted_post_ids):
                                continue
                except Exception:
                    pass
            
            # 处理 JSON 文件
            if file_path.endswith('.json'):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                
                    if isinstance(data, list):
                        original_len = len(data)
                        new_data = []
                        for item in data:
                            is_match = False
                            if str(item.get('time', '')).startswith(target_date):
                                is_match = True
                            elif str(item.get('id', '')) in deleted_post_ids or str(item.get('bid', '')) in deleted_post_ids:
                                is_match = True
                            elif str(item.get('post_id', '')) in deleted_post_ids:
                                is_match = True
                        
                            if not is_match:
                                new_data.append(item)
                            
                        if len(new_data) < original_len:
                            with open(file_path, 'w', encoding='utf-8') as f:
                                json.dump(new_data, f, ensure_ascii=False, indent=2)
                            deleted_count["json"] += (original_len - len(new_data))
                            print(f"  [JSON] 已从 {file_path} 中删除 {(original_len - len(new_data))} 条记录")
                except Exception as e:
                    print(f"  读取/修改 JSON 出错: {file_path}, 错误: {e}")
                
            # 处理 CSV 文件
            elif file_path.endswith('.csv'):
                try:
                    df = pd.read_csv(file_path, dtype=str)
                    original_len = len(df)
                
                    mask_time = pd.Series([False]*len(df), index=df.index)
                    if 'time' in df.columns:
                        mask_time = df['time'].astype(str).str.startswith(target_date)
                    
                    mask_id = pd.Series([False]*len(df), index=df.index)
                    if 'id' in df.columns:
                        mask_id = mask_id | df['id'].astype(str).isin(deleted_post_ids)
                    if 'bid' in df.columns:
                        mask_id = mask_id | df['bid'].astype(str).isin(deleted_post_ids)
                    if 'post_id' in df.columns:
                        mask_id = mask_id | df['post_id'].astype(str).isin(deleted_post_ids)
                
                    mask = mask_time | mask_id
                    new_df = df[~mask]
                    if len(new_df) < original_len:
                        new_df.to_csv(file_path, index=False, encoding='utf-8-sig')
                        deleted_count["csv"] += (original_len - len(new_df))
                        print(f"  [CSV] 已从 {file_path} 中删除 {(original_len - len(new_df))} 条记录")
                except Exception as e:
                    pass
                
            # 处理 SQLite 文件
            elif file_path.endswith('.db'):
                try:
                    conn = sqlite3.connect(file_path)
                    cursor = conn.cursor()
                
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [row[0] for row in cursor.fetchall()]
                
                    ids_tuple = tuple(deleted_post_ids) if deleted_post_ids else ('',)
                    placeholders = ','.join(['?'] * len(ids_tuple))
                
                    if 'tweets' in tables:
                        cursor.execute(f"DELETE FROM tweets WHERE time LIKE ? OR id IN ({placeholders}) OR bid IN ({placeholders})", (f"{target_date}%",) + ids_tuple + ids_tuple)
                        if cursor.rowcount > 0:
                            deleted_count["sqlite"] += cursor.rowcount
                            print(f"  [SQLite] 从 {file_path} 的 tweets 表中删除 {cursor.rowcount} 条记录")
                        
                    if 'comments' in tables:
                        cursor.execute(f"DELETE FROM comments WHERE time LIKE ? OR post_id IN ({placeholders})", (f"{target_date}%",) + ids_tuple)
                        if cursor.rowcount > 0:
                            deleted_count["sqlite"] += cursor.rowcount
                            print(f"  [SQLite] 从 {file_path} 的 comments 表中删除 {cursor.rowcount} 条记录")
                        
                    if 'replies' in tables:
                        cursor.execute(f"DELETE FROM replies WHERE time LIKE ? OR post_id IN ({placeholders})", (f"{target_date}%",) + ids_tuple)
                        if cursor.rowcount > 0:
                            deleted_count["sqlite"] += cursor.rowcount
                            print(f"  [SQLite] 从 {file_path} 的 replies 表中删除 {cursor.rowcount} 条记录")
                        
                    conn.commit()
                    conn.close()
                except Exception as e:
                    print(f"  读取/修改 SQLite 出错: {file_path}, 错误: {e}")
                
            # 处理 Markdown 文件
            elif file_path.endswith('.md') or file_path.endswith('.txt'):
                try:
                    posts = parse_markdown_posts(file_path)
                    original_len = len(posts)
                    if original_len > 0:
                        new_posts = []
                        for p in posts:
                            pid = str(p.get("id", ""))
                            time_str = p.get("time_str", "")
                            if target_date in file_path or target_date in time_str or any(d_id in pid for d_id in deleted_post_ids):
                                pass
                            else:
                                new_posts.append(p)
                            
                        if len(new_posts) < original_len:
                            if len(new_posts) == 0:
                                import os
                                os.remove(file_path)
                                deleted_count["md"] += original_len
                                print(f"  [Markdown] 已删除文件 {file_path}")
                            else:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    first_line = f.readline()
                                
                                with open(file_path, "w", encoding="utf-8") as f:
                                    if first_line.startswith("# "):
                                        f.write(first_line.strip() + "\n\n")
                                    else:
                                        f.write("# 微博存档\n\n")
                                    
                                    for p in new_posts:
                                        f.write(f"## {p['time_str']}\n\n")
                                        f.write(f"{p['body']}\n\n")
                                        f.write(f"---\n\n")
                                deleted_count["md"] += (original_len - len(new_posts))
                                print(f"  [Markdown] 已从 {file_path} 中删除 {(original_len - len(new_posts))} 条记录")
                except Exception:
                    pass

    print("\n--- 清理完成 ---")
    print(f"共删除 JSON 记录: {deleted_count['json']} 条")
    print(f"共删除 CSV 记录: {deleted_count['csv']} 条")
    print(f"共删除 SQLite 记录: {deleted_count['sqlite']} 条")
    print(f"共删除 Markdown 记录: {deleted_count['md']} 条")



if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="微博爬虫及数据管理")
    parser.add_argument("-d", "--delete", type=str, help="指定要删除的微博 post_id、bid 或日期 (YYYY-MM-DD)", default="")
    parser.add_argument("-s", "--scrape", type=str, help="指定要爬取的微博 post_id、bid、URL 或日期 (YYYY-MM-DD / MM-DD)", default="")
    parser.add_argument("-u", "--uid", type=str, help="在使用 -s 爬取指定短 ID/BID 时，强制指定所属用户的 UID", default="")
    args, unknown = parser.parse_known_args()
    
    if args.delete:
        if re.match(r'^\d{4}-\d{2}-\d{2}$', args.delete):
            delete_local_data_by_date(args.delete, target_uid=args.uid)
        elif re.match(r'^\d{2}-\d{2}$', args.delete):
            print("错误: 删除数据时不支持仅输入 MM-DD 格式的日期，请使用完整的 YYYY-MM-DD 格式。")
        else:
            delete_local_post_data(args.delete, target_uid=args.uid)
        sys.exit(0)

    try:
        if args.scrape:
            scrape_weibo_search(scrape_target=args.scrape, target_uid=args.uid)
        else:
            scrape_weibo_search()
    except KeyboardInterrupt:
        print("\n[提示] 用户中断了程序运行。")
        try:
            sys.exit(0)
        except Exception:
            pass
