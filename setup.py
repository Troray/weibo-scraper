from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="weibo-scraper",
    version="1.0.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="A Python-based Weibo scraper using Playwright",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Zhangziqiang997/weibo-scraper",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.8",
    install_requires=[
        "playwright>=1.40.0",
        "pandas>=1.5.0",
        "openpyxl>=3.0.0",
    ],
    entry_points={
        "console_scripts": [
            "weibo-scraper=scraper:main",
            "weibo-login=login:main",
        ],
    },
)