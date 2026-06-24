# Weibo Scraper

A powerful Python-based Weibo data scraping and archiving tool. It supports date range filtering, original post filtering, multi-threaded downloading of rich media assets (images, high-definition videos, Live Photos), and exporting structured results into Markdown documents, CSV spreadsheets, JSON data, and SQLite databases.

---

## 🚀 Features

1. **Persisted Session Login**: One-time QR-code scanning to capture authentication cookies and persist state in `state.json`.
2. **Batch & Incremental Crawling**: Feed a list of user IDs in `userid.txt`. The tool automatically checks for last-run timestamps, parses new posts incrementally, and updates/writes back timestamps and nicknames after every successful run.
3. **Structured Data Exports**:
   - **Markdown Archives**: Chronologically organized daily archives (`YYYY-MM-DD.md`) nested under monthly folders (`YYYY-MM/`). Post metrics are printed in a clean, human-readable format: `Engagement: reposts 12 | comments 34 | likes 56`.
   - **CSV Spreadsheets**: Columns storing Weibo ID, creation time, status link, full text content, engagement counts (`reposts_count`, `comments_count`, `attitudes_count`), and media file paths.
   - **JSON Data**: Local JSON data files created synchronously for quick access and secondary development.
   - **SQLite Databases**: Auto-generated local SQL database tables, ideal for developers wishing to write custom queries or dashboards.
4. **Silent Video & Live Photo Resolution**:
   - Spawns a background headless browser instance to load video details pages and retrieve direct 1080p/2K/4K CDN stream URLs. Downloads videos using multi-threaded chunked streams with dynamic speed metrics.
   - Automatically crawls the `.mov` format video of Live Photos and completes the download.
   - **Link Expiration Protection**: Saves official permanent webpage links (e.g. `video.weibo.com/show` or `weibo.com/tv/show`) in the SQLite and CSV outputs rather than expiring ephemeral CDN links (which return 403 after 2 hours).

---

## 🛠️ Installation

### Prerequisites
- Python 3.8+
- Chromium Browser (managed automatically by Playwright)

### Setup Steps
Run the following commands in your project directory:
```bash
pip install -r requirements.txt
playwright install chromium
```

---

## 🔑 Quick Start

### Step 1: Scan and Login
Authenticate with Weibo by running:
```bash
python login.py
```
A visible browser window will open. **Scan the QR code to log in**. Once the browser logs in and redirects to the Weibo homepage, the script will automatically close the window and save your login credentials to `state.json`.

### Step 2: Configure Settings in `config.py`
All settings are centrally managed via [config.py](./config.py):

```python
# --- Target User Configuration ---
TARGET_USER_IDS = "userid.txt"  # Can be a text file path, a single ID string, or a list of IDs

# --- Date Settings ---
START_DATE = "2025-09-09"       # Starting date (inclusive, YYYY-MM-DD)
END_DATE = ""         # Ending date. If left blank, crawls incrementally up to the current run time.

# --- Fine-grained Media Download Control (1 = Enabled, 0 = Disabled) ---
ORIGINAL_PIC_DOWNLOAD = 1          # Save images from original posts
RETWEET_PIC_DOWNLOAD = 1           # Save images from retweet posts
ORIGINAL_VIDEO_DOWNLOAD = 1        # Save videos from original posts
RETWEET_VIDEO_DOWNLOAD = 0         # Save videos from retweet posts
ORIGINAL_LIVE_PHOTO_DOWNLOAD = 1   # Save Live Photos from original posts
RETWEET_LIVE_PHOTO_DOWNLOAD = 0    # Save Live Photos from retweet posts

# --- Output Switches (1 = Enabled, 0 = Disabled) ---
ENABLE_SAVE_CSV = 1             # Save structured rows to posts.csv
ENABLE_SAVE_SQLITE = 1          # Sync records to posts.db SQLite databases
ENABLE_SAVE_MARKDOWN = 1        # Save Markdown daily archive files (date-grouped)
ENABLE_SAVE_JSON = 1            # Save backup data to posts.json

# --- Filtering ---
ONLY_ORIGINAL = 0               # 1 = original posts only, 0 = include reposts
```

### Step 3: Configure target users in `userid.txt`
If `TARGET_USER_IDS = "userid.txt"`, write target accounts inside the file (one ID per line). Comments and timestamps are auto-filled by the scraper:
```text
# ID         Nickname(Auto-filled)   Last-scraped-timestamp(Auto-filled)
6634214154
```
- **Incremental Runs**: If a row has a timestamp, the crawler uses it as the starting window for that user, ensuring only newer posts are fetched.
- **Auto-Maintenance**: After finishing a user's crawl, the script automatically parses the nickname, logs the end timestamp, and updates the row text.

### Step 4: Run the Scraper
```bash
python scraper.py
```

# 3. Re-crawl all records for a specific date
# (⚠️ Note: This will iterate through all users in userid.txt by default. YYYY-MM-DD format is required)
python scraper.py -s 2026-06-22

# (Optional) If you only want to scrape a specific date for a specific user, use the -u parameter.
# (This completely ignores the timestamp config in userid.txt and does a targeted scrape)
python scraper.py -s 2026-06-22 -u 6634214154
```

### Step 5 (Optional): Clean Up Local Data for a Specific Post
If you find that a certain post's data is no longer needed after crawling, or if you need to clear it and re-crawl due to missing data in an early run, you can use a command-line argument to quickly and thoroughly clean up the local records of that post and all its comments:
```bash
# https://weibo.com/6634214154/R4gcQiHll
python scraper.py -d R4gcQiHll
# Or pass the pure numeric Weibo ID:
python scraper.py -d 5310156124456519

# Delete all records for a specific date in bulk:
python scraper.py -d 2026-06-22

# (Optional) Precisely target a single user's records for deletion with the -u parameter:
python scraper.py -d 2026-06-22 -u 6634214154
```
A built-in conversion algorithm ensures that no matter which form of ID or YYYY-MM-DD date you provide, the program accurately scans and completely strips out the corresponding CSV, JSON, SQLite, and Markdown data.

---

## 📂 Output Folder Structure

Scraped files are organized neatly under the `weibo` directory:
```text
weibo-scraper/
├── weibo/                                  # Main output folder
│   └── User Nickname/ (e.g. 宋雨琦_i-dle)
│       ├── posts.csv                       # Struct spreadsheet summarizing user's posts
│       ├── posts.db                        # Local SQLite database
│       ├── posts.json                      # JSON backup records
│       ├── user_id.txt                     # Includes Detailed User Information fields
│       ├── avatar.jpg                      # User profile avatar
│       ├── avatar_hd.jpg                   # High-definition user profile avatar
│       ├── cover_image_phone.jpg           # User's mobile profile background cover
│       ├── cover_image_web.jpg             # User's desktop profile background cover (if different)
│       └── YYYY-MM/ (Monthly subfolder, e.g. 2025-09)
│           ├── YYYY-MM-DD.md               # Post diary (organized by day, including device, location, and comments with regions)
│           ├── img/                        # Original high-resolution images
│           ├── video/                      # Original MP4 video files
│           ├── livephoto/                  # Original Live Photo video files (.mov)
│           ├── comment/                    # Original comment images
│           └── retweet/                    # Repost media subdirectory
│               ├── img/                    # Repost images
│               ├── video/                  # Repost videos
│               └── livephoto/              # Repost Live Photos
│               └── comment/                # Repost comment images (if any)
```

### 1. Markdown Archive Layout
```markdown
# 2025-09-09 Weibo Archive

## 23:00:00

**Link:** [https://weibo.com/6634214154/5209101333955234](https://weibo.com/6634214154/5209101333955234) | Weibo ID: `5209101333955234`

**Engagement:** Reposts 61487 | Comments 34730 | Likes 306553

🥰🎵我的新歌<Gone>MV上线啦
You know I’ll always be with you, baby🩹❤️
...
<video src="./video/20250909_230000_5209101333955234_1.mp4" controls width="100%"></video>
```

### 2. Database columns (CSV & SQLite)
#### Posts Table (`posts`)
- `id` (Text Primary Key): Weibo post unique mid.
- `time` (Text): Timestamp of the post.
- `link` (Text): Stable URL linking to the post.
- `content` (Text): Full text content.
- `reposts_count` (Integer): Total repost count.
- `comments_count` (Integer): Total comment count.
- `attitudes_count` (Integer): Total like count.
- `images` (Text): Local image file paths or large image CDN urls (comma-separated).
- `videos` (Text): Official permanent webpage URLs of videos (avoiding token timeouts).
- `livephotos` (Text): Extracted Live Photo URLs (JSON string).
- `device` (Text): Publishing device.
- `ip_location` (Text): Publishing location (IP location).

#### Comments Table (`comments`)
- `id` (Text Primary Key): Comment unique ID.
- `post_id` (Text): Weibo post unique mid.
- `parent_id` (Text): Parent comment ID (empty for root comments).
- `time` (Text): Timestamp of the comment.
- `user_id` (Text): Comment user ID.
- `user_name` (Text): Comment user nickname.
- `content` (Text): Comment body text.
- `like_count` (Integer): Comment like count.
- `media_url` (Text): Comment media asset URL.
- `post_time` (Text): Timestamp of the parent post.
- `post_summary` (Text): Text summary of the parent post.
- `source` (Text): Region location of the commenter (e.g. "来自浙江").

---

## 💡 Acknowledgement

This project is a secondary development based on [Zhangziqiang997/weibo-scraper](https://github.com/Zhangziqiang997/weibo-scraper). We would like to express our sincere gratitude to the original author for their outstanding open-source contribution!