import requests
import re
import time
import argparse
import json
import logging
from urllib.parse import urlparse, urljoin, parse_qs, urlencode, urlunparse
from collections import Counter
from typing import List, Dict, Optional, Tuple, Callable, Set, Any
from bs4 import BeautifulSoup, Tag
from dataclasses import dataclass

# --- Configuration & Constants ---

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
REQUEST_TIMEOUT = 15 # seconds
API_POSTS_PER_PAGE = 100 # Max allowed by WordPress API default
INTER_REQUEST_DELAY = 0.3 # seconds, small delay between fetching posts

# Common non-post URL path patterns to ignore during HTML scraping
NON_POST_PATH_SEGMENTS = ['/page/', '/category/', '/tag/', '/author/', '/feed/', '/wp-content/', '/wp-includes/']
# Common non-post query parameters to ignore
NON_POST_QUERY_PARAMS = ['replytocom']
# Common file extensions to ignore
NON_POST_FILE_EXTENSIONS = ['.pdf', '.jpg', '.jpeg', '.png', '.gif', '.zip', '.rar', '.mp3', '.mp4']

# Selectors prioritized for guessing blog post links on listing pages
LINK_SELECTOR_PRIORITY = [
    'article a[href]',            # Links within <article> tags
    '.post a[href]',              # Links within elements with class "post"
    '.entry a[href]',             # Links within elements with class "entry"
    '.hentry a[href]',            # Links within elements with class "hentry" (common WP theme class)
    'a[rel="bookmark"]',          # Links with rel="bookmark" (often used for permalinks)
    'h2 a[href]',                 # Links within H2 tags (common for post titles)
    'h3 a[href]',                 # Links within H3 tags
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
# Regex for date patterns if selectors fail (simple patterns)
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

# --- Data Structure ---

@dataclass
class PostData:
    """Holds extracted data for a single blog post."""
    url: str
    title: Optional[str] = None
    date: Optional[str] = None
    content: Optional[str] = None

    def format_output(self) -> str:
        """Formats the post data for saving to a file."""
        title_str = self.title if self.title else "No Title Found"
        date_str = f"Date: {self.date}" if self.date else "Date: Not Found"
        content_str = self.content if self.content else "Content: Not Found"
        return f"# {title_str}\nURL: {self.url}\n{date_str}\n\n{content_str}\n\n{'='*80}\n\n"

# --- Scraper Class ---

class BlogScraper:
    """
    Scrapes blog posts from a given base URL.

    Attempts to use the WordPress REST API first. If unavailable or unsuccessful,
    it falls back to HTML scraping heuristics.
    """
    def __init__(self, base_url: str, lang: Optional[str] = None):
        """
        Initializes the scraper.

        Args:
            base_url: The starting URL of the blog (e.g., homepage or listing page).
            lang: Optional language code (e.g., 'en', 'fi') to filter posts (primarily for API).
        """
        if not base_url.startswith(('http://', 'https://')):
             raise ValueError("Base URL must start with http:// or https://")

        self.base_url = base_url
        self.lang = lang
        self.parsed_base_url = urlparse(base_url)
        self.base_domain = self.parsed_base_url.netloc
        # Define a reasonable base path for comparison (avoiding deep initial paths)
        path_parts = self.parsed_base_url.path.strip('/').split('/')
        self.potential_blog_root = '/' + '/'.join(path_parts[:1]) + '/' if path_parts[0] else '/'


        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})

        self.api_root_url: Optional[str] = None
        self.post_urls: Set[str] = set()
        self.content_selectors: Dict[str, Optional[str]] = {
            'title': None, 'date': None, 'content': None, 'date_text': None
        }
        self._api_used_successfully = False

    def _fetch_soup(self, url: str) -> Optional[BeautifulSoup]:
        """Fetches content from a URL and returns a BeautifulSoup object."""
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            # Try to detect encoding, fallback to utf-8
            encoding = response.encoding if response.encoding else 'utf-8'
            return BeautifulSoup(response.content, 'html.parser', from_encoding=encoding)
        except requests.exceptions.RequestException as e:
            logging.warning(f"Failed to fetch {url}: {e}")
            return None
        except Exception as e:
            logging.warning(f"Error parsing HTML from {url}: {e}")
            return None

    # --- API Discovery and Fetching ---

    def _find_wp_api_root(self, soup: BeautifulSoup) -> Optional[str]:
        """Searches BeautifulSoup object for the WordPress REST API link."""
        api_link_tag = soup.find('link', rel='https://api.w.org/')
        if api_link_tag and api_link_tag.get('href'):
            api_root_url = api_link_tag['href']
            # Basic validation: should end with /wp-json/ or contain it
            if '/wp-json/' in api_root_url:
                logging.info(f"Found potential WP API root via <link> tag: {api_root_url}")
                # Ensure it ends with a slash for urljoin
                return api_root_url if api_root_url.endswith('/') else api_root_url + '/'
            else:
                logging.warning(f"Potential API link found ({api_root_url}), but doesn't look like standard WP API root. Ignoring.")
        return None

    def _fetch_urls_from_api(self) -> bool:
        """
        Fetches post URLs from the discovered WP REST API endpoint.

        Handles pagination and applies language filter if specified.

        Returns:
            True if URLs were successfully fetched via API, False otherwise.
        """
        if not self.api_root_url:
            logging.error("API root URL not set, cannot fetch from API.")
            return False

        # Prefer 'posts' endpoint, but some themes might use custom ones.
        # Sticking to standard /wp/v2/posts for now.
        posts_endpoint = urljoin(self.api_root_url, 'wp/v2/posts')
        api_params: Dict[str, Any] = {
            'per_page': API_POSTS_PER_PAGE,
            '_fields': 'link,lang', # Request only necessary fields
            'page': 1
        }
        if self.lang:
            api_params['lang'] = self.lang
            logging.info(f"Applying API language filter: {self.lang}")

        total_pages = 1
        current_page = 1
        initial_request = True
        fetched_urls_count = 0

        logging.info(f"Attempting to fetch posts from WP API: {posts_endpoint}")

        while current_page <= total_pages:
            api_params['page'] = current_page
            logging.info(f"Fetching API page {current_page}/{total_pages or '?'}")
            try:
                response = self.session.get(posts_endpoint, params=api_params, timeout=REQUEST_TIMEOUT * 1.5) # Longer timeout for API
                response.raise_for_status()

                if initial_request:
                    total_pages_header = response.headers.get('X-WP-TotalPages')
                    if total_pages_header and total_pages_header.isdigit():
                        total_pages = int(total_pages_header)
                        total_items = response.headers.get('X-WP-Total', 'N/A')
                        logging.info(f"API reports {total_items} total posts across {total_pages} pages.")
                    else:
                        logging.warning("Could not determine total pages from API headers. Fetching only page 1.")
                        total_pages = 1
                    initial_request = False

                posts_data = response.json()

                if not isinstance(posts_data, list):
                    logging.warning(f"API response for page {current_page} is not a list. Stopping API fetch.")
                    # Handle potential non-standard structures if needed (like Miltton example)
                    # For simplicity, we'll only handle the standard list response now.
                    break

                page_extracted_count = 0
                for post in posts_data:
                    # Language check (API might not filter perfectly, or field might be missing)
                    post_lang = post.get('lang')
                    if self.lang and post_lang and post_lang != self.lang:
                        continue

                    url = post.get('link')
                    if url and isinstance(url, str) and url.startswith(('http://', 'https://')):
                        self.post_urls.add(url)
                        page_extracted_count += 1

                logging.info(f"Extracted {page_extracted_count} URLs from API page {current_page}.")
                fetched_urls_count += page_extracted_count

                if page_extracted_count == 0 and current_page > 1:
                    logging.info(f"No results found on API page {current_page}. Assuming end of results.")
                    break

                current_page += 1
                if current_page <= total_pages:
                    time.sleep(INTER_REQUEST_DELAY / 2) # Shorter delay between API pages

            except requests.exceptions.Timeout:
                 logging.warning(f"Timeout fetching API page {current_page}. Stopping API fetch.")
                 break
            except requests.exceptions.RequestException as e:
                logging.warning(f"Error fetching API page {current_page}: {e}. Stopping API fetch.")
                break
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding JSON from API on page {current_page}: {e}. Stopping API fetch.")
                break
            except Exception as e:
                logging.error(f"Unexpected error processing API data on page {current_page}: {e}", exc_info=True)
                break

        if fetched_urls_count > 0:
            logging.info(f"Finished API fetching. Total unique URLs found via API: {len(self.post_urls)}")
            return True
        else:
            logging.warning("API fetching finished, but no valid URLs were extracted.")
            return False

    # --- HTML Scraping Logic ---

    def _is_likely_post_url(self, url: str) -> bool:
        """Heuristically checks if a URL is likely a blog post based on structure."""
        try:
            parsed_url = urlparse(url)
            # 1. Must be on the same domain
            if parsed_url.netloc != self.base_domain:
                return False
            # 2. Should not be the base URL itself
            if url == self.base_url:
                return False
            # 3. Path should generally start with the potential blog root path
            #    and be longer (indicating a specific post)
            if not parsed_url.path.startswith(self.potential_blog_root) or \
               len(parsed_url.path) <= len(self.potential_blog_root):
                 # Allow exceptions if potential_blog_root is just '/' and path is not empty
                 if not (self.potential_blog_root == '/' and parsed_url.path != '/'):
                    return False

            # 4. Avoid common non-post path segments
            if any(segment in parsed_url.path for segment in NON_POST_PATH_SEGMENTS):
                return False
            # 5. Avoid common non-post query parameters
            query_params = parse_qs(parsed_url.query)
            if any(param in query_params for param in NON_POST_QUERY_PARAMS):
                return False
            # 6. Avoid common file extensions
            if any(parsed_url.path.lower().endswith(ext) for ext in NON_POST_FILE_EXTENSIONS):
                return False
            # 7. Avoid fragments
            if parsed_url.fragment:
                return False

            return True
        except Exception: # Catch potential errors from urlparse
            return False


    def _guess_link_selector_and_filter(self, soup: BeautifulSoup) -> Tuple[Optional[str], Optional[Callable[[str], bool]]]:
        """
        Guesses the CSS selector for blog post links based on common patterns.

        Args:
            soup: BeautifulSoup object of the listing page.

        Returns:
            A tuple containing:
              - The guessed CSS selector string (or None).
              - A filter function to validate URLs found by the selector (or None).
        """
        logging.info("Attempting to guess blog post link selector from HTML...")

        candidate_selectors: List[Tuple[str, Set[str]]] = []

        # Test prioritized selectors
        for selector in LINK_SELECTOR_PRIORITY:
            links = soup.select(selector)
            if not links: continue

            found_urls: Set[str] = set()
            logging.debug(f"  Testing selector: '{selector}' ({len(links)} links found)")
            for link in links:
                href = link.get('href')
                if not href or href.startswith(('#', 'mailto:', 'tel:', 'javascript:')): continue

                full_url = urljoin(self.base_url, href)
                if self._is_likely_post_url(full_url):
                    found_urls.add(full_url)

            if len(found_urls) > 1: # Require at least 2 potential post links
                logging.debug(f"    Selector '{selector}' yielded {len(found_urls)} potential post URLs.")
                candidate_selectors.append((selector, found_urls))
            else:
                 logging.debug(f"    Selector '{selector}' did not yield enough likely post URLs.")


        # Analyze candidates - prefer selectors finding more URLs, or having common path structure
        if candidate_selectors:
            # Simple heuristic: choose the selector that found the most valid URLs
            best_selector, best_urls = max(candidate_selectors, key=lambda item: len(item[1]))
            logging.info(f"Selected CSS selector '{best_selector}' (found {len(best_urls)} potential links).")
            # The filter function is already applied within _is_likely_post_url
            return best_selector, self._is_likely_post_url

        # Fallback: If no priority selectors worked, try the generic one
        logging.warning("No priority selectors yielded good results. Trying fallback 'a[href]'.")
        links = soup.select(FALLBACK_LINK_SELECTOR)
        found_urls_fallback: Set[str] = set()
        for link in links:
            href = link.get('href')
            if not href or href.startswith(('#', 'mailto:', 'tel:', 'javascript:')): continue
            full_url = urljoin(self.base_url, href)
            if self._is_likely_post_url(full_url):
                found_urls_fallback.add(full_url)

        if len(found_urls_fallback) > 1:
             logging.info(f"Selected fallback selector '{FALLBACK_LINK_SELECTOR}' (found {len(found_urls_fallback)} potential links).")
             return FALLBACK_LINK_SELECTOR, self._is_likely_post_url
        else:
             logging.error("Could not reliably guess a link selector via HTML. HTML scraping might fail.")
             return None, None


    def _fetch_urls_from_html(self, soup: BeautifulSoup) -> bool:
        """
        Extracts post URLs by scraping the HTML of the base URL.

        Args:
            soup: BeautifulSoup object of the base URL page.

        Returns:
            True if URL extraction via HTML was attempted (even if no URLs found), False otherwise.
        """
        logging.info("Using HTML scraping strategy to find post URLs...")
        link_selector, link_filter_func = self._guess_link_selector_and_filter(soup)

        if not link_selector or not link_filter_func:
            logging.warning("HTML scraping failed: Could not determine link selector or filter.")
            return False

        links = soup.select(link_selector)
        logging.info(f"Found {len(links)} links using selector '{link_selector}'. Filtering likely posts...")

        count = 0
        for link in links:
            href = link.get('href')
            if href:
                full_url = urljoin(self.base_url, href)
                if link_filter_func(full_url):
                    if full_url not in self.post_urls:
                        self.post_urls.add(full_url)
                        count += 1

        logging.info(f"Extracted {count} new unique URLs via HTML scraping.")
        logging.info(f"Total unique URLs after HTML scraping: {len(self.post_urls)}")
        # Note: This HTML scraping doesn't inherently support pagination or language filters.
        # It only scrapes the initial page provided.
        return True


    # --- Content Extraction ---

    def _guess_content_selectors(self, post_url: str) -> None:
        """
        Tries to guess CSS selectors for title, date, and content on a sample post page.

        Updates `self.content_selectors`.

        Args:
            post_url: The URL of a sample blog post to inspect.
        """
        logging.info(f"Attempting to guess content selectors using: {post_url}")
        soup = self._fetch_soup(post_url)
        if not soup:
            logging.warning(f"Could not fetch sample post {post_url} to guess selectors. Using defaults.")
            self.content_selectors['title'] = 'h1' # Default fallback
            self.content_selectors['content'] = FALLBACK_CONTENT_SELECTOR
            return

        # Guess Title
        for sel in TITLE_SELECTOR_PRIORITY:
            elem = soup.select_one(sel)
            if elem and elem.get_text(strip=True):
                self.content_selectors['title'] = sel
                logging.info(f"  Guessed title selector: '{sel}'")
                break
        if not self.content_selectors['title']:
            logging.warning("  Could not guess specific title selector, falling back to 'h1'.")
            self.content_selectors['title'] = 'h1'

        # Guess Date
        date_found_by_selector = False
        for sel in DATE_SELECTOR_PRIORITY:
            elem = soup.select_one(sel)
            if elem:
                text = elem.get('datetime', elem.get_text(strip=True)) # Prefer datetime attr
                if text and DATE_REGEX.search(text): # Check if text looks like a date
                     self.content_selectors['date'] = sel
                     logging.info(f"  Guessed date selector: '{sel}'")
                     date_found_by_selector = True
                     break
        # If no selector found, try regex on text near title
        if not date_found_by_selector:
            title_elem = soup.select_one(self.content_selectors['title']) if self.content_selectors['title'] else None
            search_area = title_elem.parent if title_elem else soup.body
            if search_area:
                text_near_title = search_area.get_text(" ", strip=True)[:500] # Limit search area
                match = DATE_REGEX.search(text_near_title)
                if match:
                    self.content_selectors['date_text'] = match.group(0)
                    logging.info(f"  Guessed date text via regex near title: '{match.group(0)}'")
                else:
                    logging.warning("  Could not guess date selector or find date text via regex.")
            else:
                 logging.warning("  Could not guess date selector or find date text via regex.")


        # Guess Content Area
        best_content_selector = None
        max_text_len = 0
        for sel in CONTENT_SELECTOR_PRIORITY:
            elem = soup.select_one(sel)
            if elem:
                # Check length AND avoid picking nested content areas if possible
                # (e.g., don't pick '.entry-content' if we already found 'article' and it's inside)
                is_nested = False
                if best_content_selector:
                    parent_elem = soup.select_one(best_content_selector)
                    if parent_elem and elem in parent_elem.descendants:
                       is_nested = True # This element is inside a previously found candidate

                if not is_nested:
                    text_len = len(elem.get_text(strip=True))
                    if text_len > max_text_len and text_len >= MIN_CONTENT_LENGTH:
                        max_text_len = text_len
                        best_content_selector = sel

        if best_content_selector:
            self.content_selectors['content'] = best_content_selector
            logging.info(f"  Guessed content selector: '{best_content_selector}' (length: {max_text_len})")
        else:
            logging.warning(f"  Could not guess specific content selector, falling back to '{FALLBACK_CONTENT_SELECTOR}'. Check output quality.")
            self.content_selectors['content'] = FALLBACK_CONTENT_SELECTOR


    def _extract_content_from_element(self, element: Tag) -> str:
        """Extracts and cleans text content from a selected content element."""
        content_parts = []
        # Try finding common block elements first
        block_elements = element.find_all(['p', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'blockquote', 'pre'])

        if block_elements:
            for elem in block_elements:
                 # Basic check to avoid extracting from known non-content sections within the main block
                 if not elem.find_parent(['nav', 'footer', 'header', 'aside', 'form', 'figure', 'figcaption']):
                     # Get text, preserving structure slightly better for lists
                     if elem.name in ['ul', 'ol']:
                         list_items = elem.find_all('li', recursive=False)
                         for i, item in enumerate(list_items):
                             prefix = "- " if elem.name == 'ul' else f"{i+1}. "
                             content_parts.append(prefix + item.get_text(separator=' ', strip=True))
                         content_parts.append("") # Add blank line after list
                     elif elem.name != 'li': # Avoid double-adding list items
                         content_parts.append(elem.get_text(separator=' ', strip=True))
            content = "\n\n".join(filter(None, content_parts))

        else:
            # Fallback: Get all text from the element if no specific blocks found
             logging.debug("  No specific block elements (p, h2, li...) found in content area. Extracting all text.")
             content = element.get_text(separator='\n', strip=True)

        # Basic cleaning: remove excessive newlines
        content = re.sub(r'\n{3,}', '\n\n', content).strip()
        return content

    def fetch_and_extract_content(self, url: str) -> Optional[PostData]:
        """Fetches a single post and extracts title, date, and content."""
        soup = self._fetch_soup(url)
        if not soup:
            return PostData(url=url, title="Error: Could not fetch page", content="") # Return partial data on fetch error

        post_data = PostData(url=url)

        try:
            # Extract Title
            if self.content_selectors.get('title'):
                title_elem = soup.select_one(self.content_selectors['title'])
                if title_elem:
                    post_data.title = title_elem.get_text(strip=True)

            # Extract Date
            if self.content_selectors.get('date'):
                date_elem = soup.select_one(self.content_selectors['date'])
                if date_elem:
                    # Prioritize 'datetime' attribute if available (often more structured)
                    dt = date_elem.get('datetime')
                    if dt:
                         post_data.date = dt
                    else:
                         post_data.date = date_elem.get_text(strip=True)
            elif self.content_selectors.get('date_text'): # Use regex match if found
                 post_data.date = self.content_selectors['date_text']

            # Extract Content
            if self.content_selectors.get('content'):
                content_element = soup.select_one(self.content_selectors['content'])
                if content_element:
                    post_data.content = self._extract_content_from_element(content_element)
                else:
                    logging.warning(f"Content selector '{self.content_selectors['content']}' failed for {url}")
                    post_data.content = "Error: Content selector failed."
            else:
                 post_data.content = "Error: Content selector not determined."


            return post_data

        except Exception as e:
            logging.error(f"Error parsing content from {url}: {e}", exc_info=True)
            return PostData(url=url, title="Error: Failed to parse content", content=str(e)) # Return partial data on parse error


    # --- Main Scrape Orchestration ---

    def discover_urls(self) -> None:
        """
        Discovers post URLs by trying the WP API first, then falling back to HTML scraping.
        Populates `self.post_urls`.
        """
        logging.info(f"Starting URL discovery for {self.base_url}")
        initial_soup = self._fetch_soup(self.base_url)

        if initial_soup:
            # 1. Try to find and use WP API
            self.api_root_url = self._find_wp_api_root(initial_soup)
            if self.api_root_url:
                logging.info("Attempting to fetch URLs via WP API...")
                if self._fetch_urls_from_api():
                    self._api_used_successfully = True
                    logging.info(f"Successfully retrieved {len(self.post_urls)} URLs using the API.")
                else:
                    logging.warning("WP API detected but failed to retrieve URLs. Falling back to HTML scraping.")
                    self._fetch_urls_from_html(initial_soup) # Try HTML on the same initial soup
            else:
                # 2. If no API found, use HTML scraping
                logging.info("WP API root not found. Proceeding with HTML scraping.")
                self._fetch_urls_from_html(initial_soup)
        else:
            logging.error(f"Could not fetch the initial page {self.base_url}. Cannot proceed.")

    def scrape(self) -> List[PostData]:
        """
        Orchestrates the entire scraping process: discovers URLs and extracts content.

        Returns:
            A list of PostData objects containing the scraped information.
        """
        self.discover_urls()

        if not self.post_urls:
            logging.warning("No blog post URLs were found. Aborting content extraction.")
            return []

        # Guess content selectors using the first discovered URL
        # Sort URLs for consistent selection
        sorted_urls = sorted(list(self.post_urls))
        self._guess_content_selectors(sorted_urls[0])

        logging.info("-" * 20)
        logging.info("--- Processing Individual Blog Posts ---")
        all_post_data: List[PostData] = []
        total_urls = len(sorted_urls)

        for i, url in enumerate(sorted_urls):
            logging.info(f"Processing post {i+1}/{total_urls}: {url}")
            post_data = self.fetch_and_extract_content(url)
            if post_data:
                all_post_data.append(post_data)
            # Add delay to be polite to the server
            time.sleep(INTER_REQUEST_DELAY)

        logging.info("-" * 20)
        processed_count = len(all_post_data)
        logging.info(f"Successfully processed content for {processed_count}/{total_urls} URLs.")
        return all_post_data

# --- File Saving ---

def save_posts_to_file(posts: List[PostData], filename: str, base_url: str, lang: Optional[str]) -> None:
    """Saves the extracted post data to a text file."""
    logging.info(f"Saving {len(posts)} posts to {filename}")
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            header = f"# Blog Posts Collection from: {base_url}"
            if lang:
                header += f" (Language Filter: {lang})"
            header += f"\n# Scraped on: {time.strftime('%Y-%m-%d %H:%M:%S')}"
            header += f"\n# Total Posts Found: {len(posts)}\n\n"
            f.write(header)

            for post in posts:
                f.write(post.format_output())

        logging.info(f"Content successfully saved to {filename}")
    except IOError as e:
        logging.error(f"Error saving file {filename}: {e}")
    except Exception as e:
         logging.error(f"An unexpected error occurred during file saving: {e}", exc_info=True)

# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(
        description="Scrape blog posts. Tries WP REST API first, then falls back to HTML scraping heuristics.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("base_url", help="The base URL of the blog listing page (e.g., 'https://example.com/blog').")
    parser.add_argument("-o", "--output", help="Output filename (.txt). If not provided, generates based on domain.", default=None)
    parser.add_argument("-l", "--lang", help="Optional language code filter (e.g., 'en', 'fi'). Primarily affects API requests.", default=None)
    parser.add_argument("-v", "--verbose", help="Enable debug logging.", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Debug logging enabled.")


    # Generate default filename if needed
    output_filename = args.output
    if not output_filename:
        try:
            domain = urlparse(args.base_url).netloc.replace('www.', '')
            safe_domain = re.sub(r'[^\w\-.]', '_', domain) # Sanitize domain for filename
            output_filename = f"{safe_domain}_blog_posts.txt"
        except Exception:
            logging.warning("Could not parse domain from base_url, using default filename.")
            output_filename = "blog_posts_output.txt"
        logging.info(f"Output filename not specified, using default: {output_filename}")
    elif not output_filename.lower().endswith('.txt'):
         output_filename += ".txt"


    try:
        scraper = BlogScraper(base_url=args.base_url, lang=args.lang)
        extracted_posts = scraper.scrape()

        if extracted_posts:
            save_posts_to_file(extracted_posts, output_filename, args.base_url, args.lang)
        else:
            logging.info("No posts were extracted. Nothing to save.")

    except ValueError as ve:
         logging.error(f"Configuration error: {ve}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during scraping: {e}", exc_info=True)

if __name__ == "__main__":
    main()
