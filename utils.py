import os

import builtins
import logging
from config import ENABLE_VERBOSE_LOGGING, ENABLE_FILE_LOGGING, LOG_FILE_PATH
import config

logger = logging.getLogger('weibo_scraper')
logger.setLevel(logging.INFO)

if getattr(config, 'ENABLE_FILE_LOGGING', 1):
    file_handler = logging.FileHandler(LOG_FILE_PATH, encoding='utf-8')
    file_formatter = logging.Formatter('%(asctime)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

original_print = builtins.print
from rich.console import Console
from rich.status import Status
global_console = Console()
global_dashboard = Status('[bold cyan]准备开始抓取...[/bold cyan]', console=global_console)

import builtins
import config
import sys

import re
from datetime import datetime, timedelta

def safe_filename(name: str) -> str:
    """
    将包含在文件名或目录名中的非法字符替换为下划线，确保文件路径在各个操作系统上的安全性。
    """
    if not name:
        return "Unknown"
    # Windows 和 Linux 文件系统不支持的字符
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

def parse_weibo_time(time_str, reference_date=None):
    """
    将微博的时间字符串转换为 datetime 对象。
    支持格式：
    - 刚刚
    - x分钟前
    - x小时前
    - 昨天 HH:MM
    - MM-DD HH:MM (今年)
    - YYYY-MM-DD (往年)
    - YYYY-MM-DD HH:MM
    
    Args:
        time_str: 微博时间字符串
        reference_date: 参考日期(datetime对象)，用于推断缺少年份的日期。
                        如果不提供，默认使用当前系统时间的年份。
    """
    now = datetime.now()
    # 当日期不含年份时，使用 reference_date 的年份（如果提供），否则用当前年份
    ref_year = reference_date.year if reference_date else now.year
    time_str = time_str.strip()

    if "刚刚" in time_str:
        return now

    # x分钟前
    match = re.match(r"(\d+)分钟前", time_str)
    if match:
        minutes = int(match.group(1))
        return now - timedelta(minutes=minutes)

    # x小时前
    match = re.match(r"(\d+)小时前", time_str)
    if match:
        hours = int(match.group(1))
        return now - timedelta(hours=hours)

    # 今天 HH:MM
    if "今天" in time_str:
        time_part = time_str.replace("今天", "").strip()
        try:
            t = datetime.strptime(time_part, "%H:%M")
            return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        except ValueError:
            pass

    # 昨天 HH:MM
    if "昨天" in time_str:
        time_part = time_str.replace("昨天", "").strip()
        try:
            t = datetime.strptime(time_part, "%H:%M")
            yesterday = now - timedelta(days=1)
            return yesterday.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        except ValueError:
            pass

    # MM-DD HH:MM (今年)
    # 微博显示的 "11-23 12:30" 通常指今年
    # 但如果是 "2022-11-23" 则指往年
    
    # 尝试匹配 YYYY-MM-DD
    try:
        return datetime.strptime(time_str, "%Y-%m-%d")
    except ValueError:
        pass
        
    # 尝试匹配 YYYY-MM-DD HH:MM
    try:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M")
    except ValueError:
        pass

    # 尝试匹配 MM-DD HH:MM (默认为今年)
    try:
        dt = datetime.strptime(time_str, "%m-%d %H:%M")
        return dt.replace(year=ref_year)
    except ValueError:
        pass
    
    # 尝试匹配 MM-DD (默认为今年)
    try:
        dt = datetime.strptime(time_str, "%m-%d")
        return dt.replace(year=ref_year)
    except ValueError:
        pass

    # --- 新增：处理中文日期格式 (搜索页常见) ---
    
    # YYYY年MM月DD日 HH:MM
    try:
        return datetime.strptime(time_str, "%Y年%m月%d日 %H:%M")
    except ValueError:
        pass

    # 正则匹配中文日期，解决单双数日问题 (10月4日 vs 10月04日)
    # 格式: 10月31日 18:58
    match = re.search(r"(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{1,2})", time_str)
    if match:
        month, day, hour, minute = map(int, match.groups())
        return datetime(ref_year, month, day, hour, minute, 0)

    # 格式: 10月31日 (无时间，通常默认为 00:00 或当前时间? 搜索页一般都有时间，除了很老的)
    match = re.search(r"(\d{1,2})月(\d{1,2})日", time_str)
    if match:
        month, day = map(int, match.groups())
        return datetime(ref_year, month, day, 0, 0, 0)
    # ----------------------------------------

    # 如果都匹配不上，返回 None 或当前时间（视情况而定，这里返回 None 以便报错）
    print(f"Warning: Unknown time format: {time_str}")
    return None

def print_rich(*args, **kwargs):
    """
    打印带有 Rich 语法标签的彩色文本，并自动去除标签后写入日志文件。
    这样既能保证终端的颜色高亮，又不会导致日志文件中出现 [bold red] 等字面量标签。
    """
    msg = " ".join(str(a) for a in args)
    # 简单正则去除常用的 rich 标签
    clean_msg = re.sub(r'\[/?(?:bold\s+)?(?:red|yellow|green|cyan|blue|magenta|white)\]', '', msg)
    logger.info(clean_msg.strip())
    global_console.print(*args, **kwargs)

def verbose_print(*args, **kwargs):
    msg = ' '.join((str(a) for a in args))
    logger.info(msg.strip())
    if ENABLE_VERBOSE_LOGGING:
        original_print(*args, **kwargs)
    elif msg.startswith('错误:') or msg.startswith('[提示]') or '删除' in msg or ('清理完成' in msg) or ('扫描' in msg) or ('找到' in msg) or ('跳过' in msg):
        original_print(*args, **kwargs)

def safe_filename(name):
    """
    过滤掉 Windows/Linux/Mac 中不能用于文件名或路径的安全隐患字符，
    防止恶意路径穿越（如 ../）或由于特殊字符导致程序越界崩溃。
    """
    if not name:
        return 'unknown'
    name = re.sub('[\\\\/:*?"<>|]', '_', str(name))
    name = name.replace('..', '_')
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
        if config_val.endswith('.txt'):
            if os.path.exists(config_val):
                with open(config_val, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and (not line.startswith('#')):
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
        for part in parts[1:]:
            parsed_dt = None
            for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                try:
                    parsed_dt = datetime.strptime(part, fmt)
                    break
                except ValueError:
                    continue
            if parsed_dt is not None:
                start_time = parsed_dt
            else:
                username = part
        users.append({'id': user_id, 'username': username, 'start_time': start_time})
    return users

def get_date_ranges(start_date, end_date, step_days=1):
    """
    将大时间段切分为极小的时间段（默认1天），以避免微博搜索结果被截断。
    返回格式: [("2023-01-01", "2023-01-01"), ("2023-01-02", "2023-01-02")...]
    """
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    ranges = []
    current = start
    while current <= end:
        current_end = current + timedelta(days=step_days - 1)
        if current_end > end:
            current_end = end
        ranges.append((current.strftime('%Y-%m-%d'), current_end.strftime('%Y-%m-%d')))
        current = current_end + timedelta(days=1)
    return ranges

def parse_weibo_stats(stats_text):
    """
    解析微博的转发、评论、点赞数量。
    返回: (reposts, comments, attitudes)
    """
    if not stats_text:
        return (0, 0, 0)
    stats_text = re.sub('\\s+', ' ', stats_text).strip()

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
    parts = stats_text.split()
    pure_numbers = []
    for p in parts:
        if re.match('^\\d+(\\.\\d+)?万?$', p):
            pure_numbers.append(p)
    if len(pure_numbers) == 3:
        return (parse_num(pure_numbers[0]), parse_num(pure_numbers[1]), parse_num(pure_numbers[2]))
    reposts = 0
    comments = 0
    likes = 0
    repost_match = re.search('(?:转发|转评|共转)\\s*(\\d+(?:\\.\\d+)?万?)', stats_text)
    if repost_match:
        reposts = parse_num(repost_match.group(1))
    comment_match = re.search('评论\\s*(\\d+(?:\\.\\d+)?万?)', stats_text)
    if comment_match:
        comments = parse_num(comment_match.group(1))
    like_match = re.search('(?:点赞|赞|态度)\\s*(\\d+(?:\\.\\d+)?万?)', stats_text)
    if like_match:
        likes = parse_num(like_match.group(1))
    if reposts == 0 and comments == 0 and (likes == 0):
        if len(pure_numbers) == 2:
            return (parse_num(pure_numbers[0]), parse_num(pure_numbers[1]), 0)
        elif len(pure_numbers) == 1:
            return (0, 0, parse_num(pure_numbers[0]))
    return (reposts, comments, likes)

def clean_html_preserve_emojis(html_str):
    if not html_str:
        return ''
    import re
    import html
    text = re.sub('<img[^>]*?alt=["\\\'](\\[[^"\\\'\\]]+\\])["\\\'][^>]*?>', '\\1', html_str)
    text = re.sub('<[^>]+>', '', text)
    return html.unescape(text).strip()

builtins.print = verbose_print
