# Default configuration values for BLECH scraper

# --- Request Settings ---
REQUEST_TIMEOUT = 15  # seconds
INTER_REQUEST_DELAY = 1.0 # seconds - Be polite to servers!
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; BLECH-Scraper/0.1; +https://github.com/your_username/blech)" # Update with your repo URL

# --- URL Filtering ---
# Common path segments that usually don't contain blog posts
NON_POST_PATH_SEGMENTS = [
    '/category/', '/tag/', '/author/', '/page/', '/search/',
    '/wp-content/', '/wp-includes/', '/feed/', '/rss/', '/atom/',
    '/cart/', '/checkout/', '/my-account/', '/product/', '/shop/'
]
# Common query parameters that usually indicate non-post pages
NON_POST_QUERY_PARAMS = [
    's', 'search', 'query', 'cat', 'tag', 'author', 'paged',
    'page_id', 'attachment_id', 'replytocom', 'add-to-cart'
]
# Common file extensions to ignore
NON_POST_FILE_EXTENSIONS = [
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.zip', '.css', '.js', '.xml', '.ico',
    '.mp3', '.mp4', '.avi', '.mov', '.svg', '.webp'
]

# --- Content Extraction Heuristics ---
# Common selectors for main content areas
COMMON_CONTENT_SELECTORS = [
    'article .entry-content',
    'article .post-content',
    'article .td-post-content', # Common theme selector
    '.main-content',
    '.blog-content',
    '.single-post-content',
    '.post-body',
    '#main article',
    '#content article',
    '#primary article',
    'main[role="main"]',
    'div[itemprop="articleBody"]'
]
# Common selectors for titles
COMMON_TITLE_SELECTORS = [
    'h1.entry-title',
    'h1.post-title',
    'header h1',
    'article h1',
    '.post-header h1',
    '.content-header h1',
    'h1[itemprop="headline"]'

]
# Common selectors for dates (look for <time> tags first)
COMMON_DATE_SELECTORS = [
    'time.published',
    'time.entry-date',
    'time[itemprop="datePublished"]',
    '.post-date',
    '.entry-meta .date',
    '.published',
    '.meta-date',
    '.posted-on' # Common WP theme class
]
# Fallback regex patterns for dates if selectors fail (use with caution)
DATE_REGEX_PATTERNS = [
    # ISO-like YYYY-MM-DD
    r'\b(\d{4}-\d{2}-\d{2})\b',
    # Month Day, Year (e.g., January 15, 2023)
    r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b',
    # Day Month Year (e.g., 15 January 2023)
    r'\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b',
    # MM/DD/YYYY
    r'\b(\d{1,2}/\d{1,2}/\d{4})\b',
    # DD.MM.YYYY
    r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b',
]


# --- Content Length Settings ---
# Minimum length (in characters) for text to be considered valid content
MIN_CONTENT_LENGTH = 150

# --- API Settings ---
# How many posts to fetch per API request (adjust based on API limits)
API_POSTS_PER_PAGE = 20
# Maximum number of pages to fetch from API to prevent infinite loops
API_MAX_PAGES = 10
