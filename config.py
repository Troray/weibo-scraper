# config.py
# -*- coding: utf-8 -*-

# --- 目标用户配置 ---
TARGET_USER_IDS = "userid.txt"  # 支持单个 ID (如 "7928198622")、列表 (如 ["123", "456"]) 或 txt 文件路径 (如 "userid.txt")
INCREMENTAL_LOOKBACK_DAYS = 1   # 增量爬取回溯天数。在增量抓取时，将起点向前推指定天数，以防因发布/索引延迟漏掉微博。已存在的微博会自动按 ID 去重。

# --- 时间范围 ---
START_DATE = ""       # 默认抓取开始日期 (格式: YYYY-MM-DD)。如果 userid.txt 中有更晚的时间，则以 userid.txt 为准
END_DATE = ""         # 默认抓取结束日期 (格式: YYYY-MM-DD)。如果为空，则表示抓取到当前运行时间

# --- 保存开关 ---
ENABLE_SAVE_IMAGES = 1          # 是否保存图片 (1 = 启用，0 = 禁用)
ENABLE_SAVE_VIDEOS = 1          # 是否保存视频 (1 = 启用，0 = 禁用)
ENABLE_SAVE_LIVEPHOTOS = 1      # 是否保存实况照片 (Live Photo) 的视频文件 (1 = 启用，0 = 禁用)

# --- 下载设置 ---
DOWNLOAD_MIN_MULTIPART_SIZE_MB = 15 # 大于该值 (MB) 且支持 Range 请求的文件将使用多线程下载
DOWNLOAD_NUM_THREADS = 5            # 多线程并发下载的线程数

# --- 存储格式开关 ---
ENABLE_SAVE_MARKDOWN = 1        # 是否保存微博文本数据为 Markdown 文件 (1 = 启用，0 = 禁用)
ENABLE_SAVE_CSV = 1             # 是否保存微博文本数据为 CSV 文件 (1 = 启用，0 = 禁用)
ENABLE_SAVE_SQLITE = 1          # 是否保存微博数据到 SQLite 数据库 (1 = 启用，0 = 禁用)
ENABLE_SAVE_JSON = 1            # 是否保存微博数据为 JSON 文件 (1 = 启用，0 = 禁用)

# --- 评论爬取开关 ---
ENABLE_SCRAPE_COMMENTS = 0      # 是否爬取评论区 (1 = 启用，0 = 禁用，默认禁用以防反爬)
MAX_COMMENTS_PER_POST = 1000     # 每条微博最多爬取评论数
MAX_REPLIES_PER_COMMENT = 100     # 每条主评论最多爬取的子回复数 (楼中楼)

# --- 过滤开关 ---
ONLY_ORIGINAL = 0               # 是否只爬取原创微博: 0 = 爬取全部（原创+转发），1 = 只爬取原创微博

# --- 全局路径 ---
OUTPUT_DIR = "weibo"            # 输出目录
STATE_FILE = "state.json"       # 登录状态文件
