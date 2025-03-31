import logging
import re
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

try:
    # When running as an installed package
    from . import config_defaults as config
    from .models import PostData
except ImportError:
    # When running the file directly
    from blech import config_defaults as config
    from blech.models import PostData

logger = logging.getLogger(__name__)

class BlogScraper:
    def __init__(self, base_url: str, lang: Optional[str] = None, output_filename: Optional[str] = None):
        """
        Initializes the scraper.

        Args:
            base_url: The starting URL for discovery (blog index, feed, etc.).
            lang: Optional language code for filtering (primarily for API).

        Raises:
            ValueError: If the base_url is invalid.
        """
        self.base_url = self._validate_and_normalize_url(base_url)
        self.lang = lang

        # Internal state
        self.discovered_urls: Set[str] = set()
        self.processed_urls: Set[str] = set()
        self.all_post_data: List[PostData] = []

        # Configuration from defaults
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': config.DEFAULT_USER_AGENT})

        # Parse base URL details
        parsed_uri = urlparse(self.base_url)
        self.base_scheme = parsed_uri.scheme
        self.base_domain = parsed_uri.netloc
        # Try to determine a sensible root path for relative link resolution
        self.potential_blog_root = parsed_uri.path if parsed_uri.path.endswith('/') else parsed_uri.path + '/'
        if not self.potential_blog_root.startswith('/'):
            self.potential_blog_root = '/' + self.potential_blog_root

        # API detection state
        self.api_root_url: Optional[str] = None
        self._api_used_successfully: bool = False

        # Guessed selectors
        self.content_selectors: Dict[str, Optional[str]] = {
            'title': None,
            'date': None, # Selector for date element
            'date_attr': None, # Attribute of date element (e.g., 'datetime')
            'date_text': None, # Raw date text if selector fails/not found
            'content': None,
        }

    def _validate_and_normalize_url(self, url: str) -> str:
        """Validates and normalizes a URL."""
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url # Assume https if no scheme
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid base_url: {url}. Could not parse scheme or domain.")
        return url

    def _fetch_soup(self, url: str) -> Optional[BeautifulSoup]:
        """Fetches content from a URL and returns a BeautifulSoup object."""
        try:
            response = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
            response.raise_for_status()
            # Try to detect encoding, fallback to utf-8
            encoding = response.encoding if response.encoding else 'utf-8'
            # Use response.content and decode manually for better encoding handling
            soup = BeautifulSoup(response.content, 'html.parser', from_encoding=encoding)
            return soup
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing HTML for {url}: {e}")
            return None

    # --- API Discovery ---

    def _discover_wp_api(self) -> None:
        """Tries to find the WP REST API root URL from the base URL."""
        logger.debug(f"Checking for WP REST API at {self.base_url}")
        try:
            response = self.session.head(self.base_url, timeout=config.REQUEST_TIMEOUT, allow_redirects=True)
            response.raise_for_status()
            links = response.links
            if 'https://api.w.org/' in links:
                self.api_root_url = links['https://api.w.org/']['url']
                logger.info(f"Discovered WP REST API endpoint: {self.api_root_url}")
                return

            # Fallback: Check common path
            potential_api_url = urljoin(self.base_url, '/wp-json/')
            response = self.session.head(potential_api_url, timeout=config.REQUEST_TIMEOUT)
            if response.status_code == 200:
                 self.api_root_url = potential_api_url
                 logger.info(f"Found potential WP REST API endpoint via common path: {self.api_root_url}")
                 return

        except requests.exceptions.RequestException as e:
            logger.debug(f"Could not check for WP API via HEAD request: {e}")

        # Final check: fetch base URL HTML and look for <link rel="https://api.w.org/">
        soup = self._fetch_soup(self.base_url)
        if soup:
            link_tag = soup.find('link', rel='https://api.w.org/')
            if link_tag and link_tag.get('href'):
                self.api_root_url = link_tag['href']
                logger.info(f"Discovered WP REST API endpoint via <link> tag: {self.api_root_url}")

        if not self.api_root_url:
            logger.info("No WP REST API endpoint discovered.")

    def _fetch_posts_page_from_api(self, page: int) -> Optional[List[Dict[str, Any]]]:
        """Fetches a single page of posts from the WP REST API."""
        if not self.api_root_url:
            return None
        posts_endpoint = urljoin(self.api_root_url, 'wp/v2/posts')
        params = {'page': page, 'per_page': config.API_POSTS_PER_PAGE, '_embed': 'true'}
        if self.lang:
            params['lang'] = self.lang

        try:
            logger.debug(f"Requesting API: {posts_endpoint} with params: {params}")
            response = self.session.get(posts_endpoint, params=params, timeout=config.REQUEST_TIMEOUT)
            response.raise_for_status()
            # Check if response is actually JSON
            if 'application/json' not in response.headers.get('Content-Type', ''):
                logger.error("Expected JSON response, but got something else")
                return None
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Error processing API response: {e}")
            return None

    def _fetch_urls_from_api(self) -> bool:
        """Fetches all post URLs from the WP REST API using pagination."""
        all_posts: List[Dict[str, Any]] = []
        page = 1
        max_pages = config.API_MAX_PAGES

        while page <= max_pages:
            logger.info(f"Fetching API page {page}...")
            posts = self._fetch_posts_page_from_api(page)
            if posts:
                all_posts.extend(posts)
                # Be polite between API calls
                time.sleep(config.INTER_REQUEST_DELAY / 2)
            elif posts == []:
                logger.info("Reached end of API results.")
                break
            else:
                logger.warning(f"Failed to fetch API page {page}, stopping API fetch.")
                break
            page += 1

            if page > max_pages:
                 logger.warning(f"Reached maximum API page limit ({max_pages}).")
                 break

        if not all_posts:
            logger.info("No posts found via the API.")
            return False

        api_urls: Set[str] = set()
        for post in all_posts:
            url = post.get('link')
            if url:
                # Basic validation - is it a valid, absolute URL?
                parsed = urlparse(url)
                if parsed.scheme and parsed.netloc:
                    api_urls.add(url)
                else:
                    logger.debug(f"Ignoring invalid URL from API: {url}")

        if api_urls:
            logger.info(f"Found {len(api_urls)} potential post URLs via API.")
            self.discovered_urls.update(api_urls)
            self._api_used_successfully = True
            return True
        else:
            logger.info("No valid post URLs extracted from API data.")
            return False

    # --- HTML Discovery & Filtering ---

    def _is_likely_post_url(self, url: str, current_page_url: str) -> bool:
        """
        Heuristically checks if a URL found on `current_page_url` is likely a blog post.
        """
        try:
            # Resolve relative URLs relative to the page they were found on
            absolute_url = urljoin(current_page_url, url)
            parsed_url = urlparse(absolute_url)

            # 1. Must have http/https scheme
            if parsed_url.scheme not in ['http', 'https']:
                return False

            # 2. Must be on the same *effective* domain (ignore www.)
            target_domain = parsed_url.netloc.replace('www.', '')
            base_domain_no_www = self.base_domain.replace('www.', '')
            if target_domain != base_domain_no_www:
                return False

            # 3. Should not be the base URL itself (unless base URL is a single post)
            #    This needs context - we might allow it if base_url IS the only post.
            #    For now, assume index pages aren't single posts.
            if absolute_url == self.base_url:
                 return False

            # 4. Path should generally be longer than the root path found initially
            #    (Handles cases where blog is in subfolder like /blog/)
            if not parsed_url.path or not parsed_url.path.startswith(self.potential_blog_root):
                 # Allow exceptions if potential_blog_root is just '/'
                 if not (self.potential_blog_root == '/' and parsed_url.path != '/'):
                     return False
            # Check path length relative to potential root
            if len(parsed_url.path) <= len(self.potential_blog_root):
                 # Allow if potential_blog_root is '/' and path is not just '/'
                 if not (self.potential_blog_root == '/' and parsed_url.path != '/'):
                     return False

            # 5. Avoid common non-post path segments
            if any(segment in parsed_url.path for segment in config.NON_POST_PATH_SEGMENTS):
                return False
            # 6. Avoid common non-post query parameters
            query_params = parse_qs(parsed_url.query)
            if any(param in query_params for param in config.NON_POST_QUERY_PARAMS):
                return False
            # 7. Avoid common file extensions
            if any(parsed_url.path.lower().endswith(ext) for ext in config.NON_POST_FILE_EXTENSIONS):
                return False
            # 8. Avoid fragments (unless they are the only difference from base_url?) - usually indicates same-page links
            if parsed_url.fragment:
                return False

            return True
        except Exception as e:
            logger.debug(f"Error parsing or validating URL '{url}' relative to '{current_page_url}': {e}")
            return False

    def _find_post_links_on_page(self, soup: BeautifulSoup, page_url: str) -> None:
        """Scans a page's soup for likely post links and adds them to discovered_urls."""
        found_count = 0
        links = soup.find_all('a', href=True)
        logger.debug(f"Found {len(links)} total links on {page_url}")

        for link in links:
            href = link['href']
            if self._is_likely_post_url(href, page_url):
                absolute_url = urljoin(page_url, href)
                if absolute_url not in self.discovered_urls and absolute_url not in self.processed_urls:
                    self.discovered_urls.add(absolute_url)
                    found_count += 1
                    logger.debug(f"Found potential post link: {absolute_url}")

        logger.info(f"Added {found_count} new potential post URLs from {page_url}")

    def _scrape_html_for_links(self) -> bool:
        """Scrapes the initial base_url for post links if API fails."""
        logger.info(f"Attempting to find post links via HTML scraping starting from {self.base_url}")
        soup = self._fetch_soup(self.base_url)
        if not soup:
            logger.error(f"Could not fetch or parse the base URL: {self.base_url}. Cannot scrape for links.")
            return False

        initial_count = len(self.discovered_urls)
        self._find_post_links_on_page(soup, self.base_url)

        return len(self.discovered_urls) > initial_count

    # --- Content Extraction ---

    def _guess_content_selectors(self, sample_url: str) -> None:
        """
        Tries to guess CSS selectors for title, date, and content elements
        by analyzing the structure of a sample post page.
        Uses common patterns defined in config.
        """
        logger.info(f"Attempting to guess content selectors using sample URL: {sample_url}")
        soup = self._fetch_soup(sample_url)
        if not soup:
            logger.warning("Could not fetch sample URL to guess selectors.")
            return

        # 1. Guess Title Selector
        found_title = False
        title_selectors = config.COMMON_TITLE_SELECTORS + ['h1']
        for selector in title_selectors:
            element = soup.select_one(selector)
            if element and len(element.get_text(strip=True)) > 3:
                self.content_selectors['title'] = selector
                found_title = True
                logger.debug(f"Guessed title selector: {selector}")
                break

        # 2. Guess Date Selector (prioritize <time> tags)
        found_date = False
        date_selectors = config.COMMON_DATE_SELECTORS
        for selector in date_selectors:
            element = soup.select_one(selector)
            if element:
                if element.name == 'time' and element.has_attr('datetime'):
                    self.content_selectors['date'] = selector
                    self.content_selectors['date_attr'] = 'datetime'
                    found_date = True
                    break

        # If no <time datetime> found, check again for any date selector match just for text
        if not found_date:
            for selector in date_selectors:
                 element = soup.select_one(selector)
                 if element and len(element.get_text(strip=True)) > 4:
                     self.content_selectors['date'] = selector
                     self.content_selectors['date_attr'] = None
                     found_date = True
                     break

        if found_date and self.content_selectors['date']:
             logger.debug(f"Guessed date selector: {self.content_selectors['date']} (Attribute: {self.content_selectors['date_attr']})")

        # 3. Guess Content Selector
        found_content = False
        content_selectors = config.COMMON_CONTENT_SELECTORS + ['article', 'main']
        for selector in content_selectors:
            element = soup.select_one(selector)
            # Basic validation: Element exists and has substantial text content
            if element and len(element.get_text(strip=True)) > config.MIN_CONTENT_LENGTH:
                self.content_selectors['content'] = selector
                found_content = True
                logger.debug(f"Guessed content selector: {selector}")
                break

        if not found_title or not found_content:
            logger.warning("Incomplete content selectors guessed")

    def _extract_post_data(self, url: str, soup: BeautifulSoup) -> Optional[PostData]:
        """Extracts title, date, and content from a post's soup using guessed selectors."""
        title, date_str, content = None, None, None

        # Extract Title
        if self.content_selectors['title']:
            element = soup.select_one(self.content_selectors['title'])
            if element:
                title = element.get_text(strip=True)

        # Fallback title extraction if no title found with guessed selectors
        if not title:
            # Try to find any H1 tag
            h1_tags = soup.find_all('h1')
            if h1_tags:
                # If multiple H1 tags, use heuristics to select the most likely title
                # For now, just use the first one that has substantial text
                for h1 in h1_tags:
                    h1_text = h1.get_text(strip=True)
                    if len(h1_text) > 3 and len(h1_text) < 200:  # Reasonable title length
                        title = h1_text
                        logger.debug(f"Using fallback H1 tag for title: {title[:50]}...")
                        break

        # Extract Date
        if self.content_selectors['date']:
            element = soup.select_one(self.content_selectors['date'])
            if element:
                attr = self.content_selectors['date_attr']
                if attr and element.has_attr(attr):
                    date_str = element[attr]
                else: # Get text if attribute not specified or not found
                    date_str = element.get_text(strip=True)
        elif self.content_selectors['date_text']: # Use regex match if available
             date_str = self.content_selectors['date_text']

        # Extract Content
        content_extracted = False
        if self.content_selectors['content']:
            element = soup.select_one(self.content_selectors['content'])
            if element:
                # Basic cleanup - get text, separate paragraphs
                paragraphs = element.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'li', 'pre']) # Common text block tags
                if paragraphs:
                     content = "\n\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
                     content_extracted = bool(content)

                # Fallback to all text if no block tags found or no content extracted
                if not content_extracted:
                    content = element.get_text(strip=True, separator='\n')
                    content_extracted = len(content) > config.MIN_CONTENT_LENGTH

        # Fallback content extraction if no content found with guessed selectors
        if not content_extracted:
            # Try main tag first
            main_tag = soup.find('main')
            if main_tag:
                # Try to find paragraphs within main
                paragraphs = main_tag.find_all(['p', 'h2', 'h3', 'h4', 'li', 'pre'])
                if paragraphs:
                    content = "\n\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
                    content_extracted = bool(content)

                # If still no content, try to get all text from main
                if not content_extracted:
                    content = main_tag.get_text(strip=True, separator='\n')
                    content_extracted = len(content) > config.MIN_CONTENT_LENGTH

                if content_extracted:
                    logger.debug(f"Extracted content from main tag, length: {len(content)}")

            # If still no content, try article tag
            if not content_extracted:
                article_tags = soup.find_all('article')
                for article in article_tags:
                    paragraphs = article.find_all(['p', 'h2', 'h3', 'h4', 'li', 'pre'])
                    if paragraphs:
                        content = "\n\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
                        content_extracted = bool(content)
                        if content_extracted:
                            logger.debug(f"Extracted content from article tag, length: {len(content)}")
                            break

        # Basic validation: Need at least URL and some content or title
        if content or title:
            logger.debug(f"Extracted - Title: {title is not None}, Date: {date_str is not None}, Content: {content is not None} from {url}")
            return PostData(url=url, title=title, date=date_str, content=content)
        else:
            logger.warning(f"Could not extract sufficient data (title/content) from {url} using guessed selectors.")
            return None

    def _fetch_and_extract_posts(self) -> None:
        """Iterates through discovered URLs, fetches content, and extracts data."""
        if not self.discovered_urls:
             logger.warning("No potential post URLs were discovered. Cannot extract posts.")
             return

        # Use the first discovered URL to guess selectors if not already done (e.g., by API)
        if not self._api_used_successfully and not any(self.content_selectors.values()):
            sample_url = next(iter(self.discovered_urls)) # Get an arbitrary URL from the set
            self._guess_content_selectors(sample_url)

        logger.info(f"Fetching content for {len(self.discovered_urls)} discovered URLs...")
        for url in list(self.discovered_urls): # Iterate over a copy for safe removal
            if url in self.processed_urls:
                continue

            logger.debug(f"Processing URL: {url}")
            soup = self._fetch_soup(url)
            if soup:
                post_data = self._extract_post_data(url, soup)
                if post_data:
                    self.all_post_data.append(post_data)
            else:
                 logger.warning(f"Skipping post data extraction for {url} due to fetch/parse error.")

            self.processed_urls.add(url)
            # Be polite between fetching full post pages
            time.sleep(config.INTER_REQUEST_DELAY)

    def run(self) -> List[PostData]:
        """Executes the full scraping process."""
        self._discover_wp_api()
        if self.api_root_url:
            self._fetch_urls_from_api() # Populates self.discovered_urls if successful

        if not self._api_used_successfully:
            logger.info("API not found or failed, falling back to HTML link discovery.")
            self._scrape_html_for_links() # Adds to self.discovered_urls

        self._fetch_and_extract_posts() # Fetches content and extracts data

        return self.all_post_data 
