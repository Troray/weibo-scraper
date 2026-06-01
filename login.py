import json
import os
from playwright.sync_api import sync_playwright
import config

def run_login():
    """
    启动浏览器，人工扫码登录，保存 Cookie 和 Storage State。
    """
    # 确保保存路径存在
    state_path = config.STATE_FILE

    
    with sync_playwright() as p:
        # 启动带界面的浏览器 (headless=False)
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        
        page = context.new_page()
        
        print("正在打开微博登录页...")
        page.goto("https://weibo.com/login.php")
        
        print("请在浏览器中手动扫码登录...")
        print("登录成功后，程序将自动检测并保存状态。")
        
        # 等待登录成功
        # 我们可以检测某个登录后才有的元素，或者等待 URL 变化
        # 这里简单处理：等待用户跳转到首页 (weibo.com) 且没有 login 字样，或者等待用户按回车确认
        # 更稳健的方式是轮询检查 cookies 中是否包含特定的登录 cookie (如 SUB)
        
        try:
            # 等待直到 URL 包含 weibo.com 且不包含 login.php，或者等待特定的登录后元素
            # 这里设置一个较长的超时时间，让用户有时间扫码
            page.wait_for_url("https://weibo.com/", wait_until="domcontentloaded", timeout=300000) # 5分钟超时
            
            # 再次确认是否真的登录了（检查是否有头像元素等）
            # page.wait_for_selector("div.gn_position", timeout=10000) 
            
            print("检测到页面跳转，保存登录状态...")
            
            # 保存 storage state (包含 cookies 和 local storage)
            context.storage_state(path=state_path)
            print(f"登录状态已保存至: {os.path.abspath(state_path)}")
            
        except Exception as e:
            print(f"登录过程中发生错误或超时: {e}")
        
        finally:
            browser.close()

if __name__ == "__main__":
    run_login()
