# 微博数据采集与归档工具 (Weibo Scraper)

这是一个基于 Python 和 Playwright 的微博用户数据抓取工具。支持按时间范围筛选、原创微博过滤、多媒体文件（图片、超高清视频、实况照片 Live Photo）多线程下载，并支持将数据导出为 Markdown 日记本、CSV 数据表格及 SQLite 数据库。

本工具针对微博晚间发布微博的时区与索引偏差问题，实现了自动向后延展并二次比对的时间边界校准，且针对大流量超高清视频提取设计了后台无头（Headless）静默解析机制，不影响您的日常工作。

---

## 🚀 核心功能

1. **自动登录与状态持久化**：支持扫码登录，保存状态后自动复用登录态。
2. **多用户批量与增量采集**：通过 `userid.txt` 进行多用户行式配置，自动解析、记录并回写每次成功采集的最新时间戳，实现无人值守的增量更新。
3. **精细化数据字段导出**：
   - **Markdown 存档**：按用户和月份自动生成日历化归档目录。微博文本结构化排版，互动数据以极高可读性的中文形式（`转发 12 | 评论 34 | 点赞 56`）展示。
   - **CSV 数据表**：将微博 ID、发布时间、微博链接、正文内容、互动字段（`reposts_count`、`comments_count`、`attitudes_count`）以及媒体文件路径分列存储。
   - **SQLite 数据库**：同步建立本地 SQL 数据库结构，方便进行高效的数据查询及后期二次开发。
4. **视频与实况照片 (Live Photo) 静默解析与下载**：
   - 后台自动调用无头浏览器，提取最高 1080p/2K/4K 的超高清视频流，采用多线程分块并发下载，支持网速监控与断点恢复。
   - 自动通过接口抓取实况照片的 `.mov` 格式动作视频并完成下载。
   - **稳定链接保护**：CSV 和 SQLite 数据库中只保存微博官方永久视频页面链接（`video.weibo.com/show` 或 `weibo.com/tv/show`），有效解决带防盗链 Token 临时直链（2小时后失效 403）的问题。

---

## 🛠️ 环境准备

### 依赖项
- Python 3.8+
- Chromium 浏览器（Playwright 自动管理）

### 安装步骤
在项目根目录运行：
```bash
pip install -r requirements.txt
playwright install chromium
```

---

## 🔑 使用步骤

### 第一步：扫码登录
运行登录脚本：
```bash
python login.py
```
此时系统会弹出一个 Chromium 浏览器窗口，请在登录页**手动进行扫码登录**。当页面跳转至微博首页且控制台提示保存成功后，窗口会自动关闭，登录态会被持久化在本地的 `state.json` 文件中。

### 第二步：修改配置 `config.py`
全局配置统一通过根目录下的 [config.py](./config.py) 进行管理：

```python
# --- 目标用户配置 ---
TARGET_USER_IDS = "userid.txt"  # 可配置为 txt 配置文件路径 (如 "userid.txt")、单个 ID 字符串或 ID 数组

# --- 时间范围 ---
START_DATE = "2025-09-09"       # 抓取起始日期 (格式: YYYY-MM-DD)
END_DATE = ""         # 抓取结束日期。如果为空，则表示增量采集直至当前运行时间

# --- 媒体下载精细控制 (1 = 启用，0 = 禁用) ---
ORIGINAL_PIC_DOWNLOAD = 1          # 是否保存原创微博图片
RETWEET_PIC_DOWNLOAD = 1           # 是否保存转发微博图片
ORIGINAL_VIDEO_DOWNLOAD = 1        # 是否保存原创微博视频
RETWEET_VIDEO_DOWNLOAD = 0         # 是否保存转发微博视频
ORIGINAL_LIVE_PHOTO_DOWNLOAD = 1   # 是否保存原创微博Live Photo
RETWEET_LIVE_PHOTO_DOWNLOAD = 0    # 是否保存转发微博Live Photo

# --- 存储格式及输出保存开关 (1 = 启用，0 = 禁用) ---
ENABLE_SAVE_CSV = 1             # 导出 CSV 数据表 (posts.csv)
ENABLE_SAVE_SQLITE = 1          # 同步写入 SQLite 数据库 (posts.db)
ENABLE_SAVE_MARKDOWN = 1        # 保存 Markdown 日报文件 (按日期归档)
ENABLE_SAVE_JSON = 1            # 保存 JSON 数据文件 (posts.json)

# --- 过滤开关 ---
ONLY_ORIGINAL = 0               # 1 = 仅采集原创微博，0 = 采集全部微博（包括转发）
```

### 第三步：配置批量目标用户 `userid.txt`
如果您在 `config.py` 中将 `TARGET_USER_IDS` 指定为 `"userid.txt"`，可通过本文件配置需要抓取的用户。
支持的格式为`每行一个用户 ID`，支持备注及上次成功采集的 ISO 时间戳，例如：

```text
# 用户 ID   备注(自动填充)   上次抓取截止时间(自动填充)
6634214154
```
- **增量运行**：若某行配置了时间戳，则程序将自动将该时间戳作为下一次增量爬取的起始时间，仅抓取新内容。
- **自动维护**：每次对该博主成功采集后，程序会自动获取其微博昵称写在第二列，并将当前运行时间回写至第三列，实现增量状态的自动记录。

### 第四步：运行爬虫
```bash
python scraper.py
```
爬虫运行后将展示实时抓取进度，并利用多线程并行下载高清视频等媒体资源，最后整理写入对应的磁盘目录。

---

## 📂 输出结构说明

采集后的成果文件结构设计如下：
```text
weibo-scraper/
├── weibo/                                  # 核心数据输出目录
│   └── 对应博主昵称/ (如: 宋雨琦_i-dle)
│       ├── posts.csv                       # 用户全部微博结构化汇总数据表
│       ├── posts.db                        # 本地 SQLite 数据库
│       ├── posts.json                      # JSON 格式备份数据
│       ├── 对应博主id.txt                   # 包含博主详细个人资料
│       └── YYYY-MM/ (月度归档，如: 2025-09)
│           ├── YYYY-MM-DD.md               # 微博日记本 (按天归档，包含发布设备、发布位置以及附带地点的评论列表)
│           ├── img/                        # 原创微博高清大图目录
│           ├── video/                      # 原创微博超高清视频文件
│           ├── livephoto/                  # 原创实况照片动作视频目录 (.mov)
│           ├── comment/                    # 评论区中图片、动图目录
│           └── retweet/                    # 转发微博的媒体资源存放目录
│               ├── img/                    # 转发微博图片
│               ├── video/                  # 转发微博视频
│               └── livephoto/              # 转发微博实况照片
│               └── comment/                # 转发微博评论媒体 (如果存在)
```

### 1. Markdown 归档排版样例
```markdown
# 2025-09-09 微博存档

## 23:00:00

**微博链接:** [https://weibo.com/6634214154/5209101333955234](https://weibo.com/6634214154/5209101333955234) | 微博ID: `5209101333955234`

**互动数据:** 转发 61487 | 评论 34730 | 点赞 306553

🥰🎵我的新歌<Gone>MV上线啦
You know I’ll always be with you, baby🩹❤️
...
<video src="./video/20250909_230000_5209101333955234_1.mp4" controls width="100%"></video>
```

### 2. CSV / SQLite 字段结构
#### 微博表 (posts)
- `id` (文本主键): 微博 mid
- `time` (文本): 微博发布时间
- `link` (文本): 微博正文链接
- `content` (文本): 微博全文
- `reposts_count` (整数): 转发总数
- `comments_count` (整数): 评论总数
- `attitudes_count` (整数): 点赞总数
- `images` (文本): 采集到的本地图片路径，或大图 CDN URL (逗号分割)
- `videos` (文本): 保存的官方永久视频播放页地址 (防盗链接防失效)
- `livephotos` (文本): 采集到的实况照片视频 URL (JSON)
- `device` (文本): 微博发布设备
- `ip_location` (文本): 微博发布位置 (IP 属地)

#### 评论表 (comments)
- `id` (文本主键): 评论 id
- `post_id` (文本): 微博 mid
- `parent_id` (文本): 父评论 id (主评论为空)
- `time` (文本): 评论时间
- `user_id` (文本): 评论用户 id
- `user_name` (文本): 评论用户昵称
- `content` (文本): 评论内容
- `like_count` (整数): 点赞数
- `media_url` (文本): 评论附带的媒体链接
- `post_time` (文本): 对应微博的时间
- `post_summary` (文本): 对应微博的内容摘要
- `source` (文本): 评论来源/地区 (如 "来自浙江")

---

## 💡 致谢

本项目基于 [Zhangziqiang997/weibo-scraper](https://github.com/Zhangziqiang997/weibo-scraper) 项目进行二次开发，在此对原作者的优秀开源工作表示衷心的感谢！

