# config.py
# -*- coding: utf-8 -*-

# --- 目标用户配置 ---
TARGET_USER_IDS = "userid.txt"  # 支持单个 ID (如 "6634214154")、列表 (如 ["123", "456"]) 或 txt 文件路径 (如 "userid.txt")
# --- 时间范围 ---
START_DATE = "2026-06-07"       # 默认抓取开始日期 (格式: YYYY-MM-DD)。如果 userid.txt 中有更晚的时间，则以 userid.txt 为准
END_DATE = ""         # 默认抓取结束日期 (格式: YYYY-MM-DD)。如果为空，则表示抓取到当前运行时间

# --- 过滤开关 ---
# 只有设置为0的时候才会解析转发的媒体文件
ONLY_ORIGINAL = 1               # 是否只爬取原创微博: 0 = 爬取全部（原创+转发），1 = 只爬取原创微博


# --- 下载设置 ---
# 原创
ORIGINAL_PIC_DOWNLOAD = 1          # 是否保存原创微博图片 (1 = 保存，0 = 不保存)
ORIGINAL_VIDEO_DOWNLOAD = 1        # 是否保存原创微博视频 (1 = 保存，0 = 不保存)
ORIGINAL_LIVE_PHOTO_DOWNLOAD = 1   # 是否保存原创微博Live Photo (1 = 保存，0 = 不保存)
# 转发
RETWEET_PIC_DOWNLOAD = 0           # 是否保存转发微博图片 (1 = 保存，0 = 不保存)
RETWEET_VIDEO_DOWNLOAD = 0         # 是否保存转发微博视频 (1 = 保存，0 = 不保存)
RETWEET_LIVE_PHOTO_DOWNLOAD = 0    # 是否保存转发微博Live Photo (1 = 保存，0 = 不保存)


# --- 下载设置 ---
DOWNLOAD_MIN_MULTIPART_SIZE_MB = 15 # 大于该值 (MB) 且支持 Range 请求的文件将使用多线程下载
DOWNLOAD_NUM_THREADS = 10            # 多线程并发下载的线程数
DOWNLOAD_NUM_CONCURRENT_MEDIA = 20   # 同时并发下载的媒体文件数 (图片、视频、评论图片等，仅在非多线程大文件下载时生效)

# --- 日志设置 ---
ENABLE_FILE_LOGGING = 1         # 是否保存运行日志到本地文件 (1 = 是，0 = 否)
ENABLE_VERBOSE_LOGGING = 0      # 是否在控制台详细打印每条微博的抓取日志 (1 = 是，0 = 否，默认为 0 以保持控制台整洁)
LOG_FILE_PATH = 'weibo.log'     # 后台日志文件路径

# --- 存储格式开关 ---
# 最少必须启用一项，否则无法保存数据
ENABLE_SAVE_MARKDOWN = 1        # 是否保存微博文本数据为 Markdown 文件 (1 = 启用，0 = 禁用)
ENABLE_SAVE_CSV = 1             # 是否保存微博文本数据为 CSV 文件 (1 = 启用，0 = 禁用)
ENABLE_SAVE_SQLITE = 0          # 是否保存微博数据到 SQLite 数据库 (1 = 启用，0 = 禁用)
ENABLE_SAVE_JSON = 1            # 是否保存微博数据为 JSON 文件 (1 = 启用，0 = 禁用)

# --- 评论爬取开关 ---
ENABLE_SCRAPE_COMMENTS = 0      # 是否爬取评论区 (1 = 启用，0 = 禁用，默认禁用以防反爬)
MAX_COMMENTS_PER_POST = 1000     # 每条微博最多爬取评论数
MAX_REPLIES_PER_COMMENT = 500     # 每条主评论最多爬取的子回复数 (楼中楼)
ENABLE_SAVE_COMMENT_MEDIA = 1    # 是否保存评论区中的图片、动图媒体文件 (1 = 启用，0 = 禁用)
COMMENT_FLOW = 1                # 评论爬取排序: 0 = 热门评论 (数量较少), 1 = 按时间排序 (数量更多，如需爬取全部评论建议设为 1)

# --- 数据保存周期 ---
# 可选值: 
# "global"  - 仅在用户根目录下保存 cumulative (全局累计) 导出的文件 (posts.csv, posts.db, posts.json 等)
# "monthly" - 仅按月保存在月度文件夹中，例如: weibo/用户名/2026-06/posts_2026-06.csv
# "both"    - 同时保存全局累计文件和月度分割文件
SAVE_DATA_BY_PERIOD = "monthly"


# --- 全局路径 ---
OUTPUT_DIR = "weibo"            # 输出目录
STATE_FILE = "state.json"       # 登录状态文件

# --- 抓取延时配置 ---
COMMENT_PAGE_DELAY = 0.3          # 爬取主评论分页时的延时（秒），默认 0.3
REPLY_PAGE_DELAY = 0.15           # 爬取子回复（楼中楼）分页时的延时（秒），默认 0.15

