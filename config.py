import re

# --- Network & Timing ---
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
REQUEST_TIMEOUT = 15
API_POSTS_PER_PAGE = 100
INTER_REQUEST_DELAY = 0.3 # Delay between fetching posts

# --- URL Filtering ---
# Common non-post URL path patterns to ignore during HTML scraping
NON_POST_PATH_SEGMENTS = ['/page/', '/category/', '/tag/', '/author/', '/feed/', '/wp-content/', '/wp-includes/']
# Common non-post query parameters to ignore
NON_POST_QUERY_PARAMS = ['replytocom']
# Common file extensions to ignore
NON_POST_FILE_EXTENSIONS = ['.pdf', '.jpg', '.jpeg', '.png', '.gif', '.zip', '.rar', '.mp3', '.mp4']

# --- Selector Guessing ---
# Selectors prioritized for guessing blog post links on listing pages
LINK_SELECTOR_PRIORITY = [
    'article a[href]',
    '.post a[href]',
    '.entry a[href]',
    '.hentry a[href]',
    'a[rel="bookmark"]',
    'h2 a[href]',
    'h3 a[href]',
]
# Fallback selector if priority ones fail
FALLBACK_LINK_SELECTOR = 'a[href]'

# Selectors prioritized for guessing post titles
TITLE_SELECTOR_PRIORITY = [
    'h1.entry-title', 'h1.post-title', 'h1[itemprop="headline"]', 'h1'
]

# Selectors prioritized for guessing post dates
DATE_SELECTOR_PRIORITY = [
    'time[datetime]', 'span.date', 'div.post-date', 'p.date',
    '.published', '.entry-date', 'time.published'
]
# Regex for date patterns if selectors fail
DATE_REGEX = re.compile(r'\b(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\w+ \d{1,2},? \d{4})\b')

# Selectors prioritized for guessing post content areas
CONTENT_SELECTOR_PRIORITY = [
    '.entry-content', '.post-content', '.post-body', '.article-content',
    'article', 'div[itemprop="articleBody"]', '.blog-content',
    'div[role="main"]', 'main'
]
# Minimum text length to consider a content area valid
MIN_CONTENT_LENGTH = 200
# Fallback content selector
FALLBACK_CONTENT_SELECTOR = 'body' 
