import logging
import re
import time
import hashlib
import os
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
    def __init__(self, base_url: str, lang: Optional[str] = None, output_filename: Optional[str] = None,
                 max_pages: Optional[int] = None, start_page: int = 1, end_page: Optional[int] = None):
        """
        Initializes the scraper.

        Args:
            base_url: The starting URL for discovery (blog index, feed, etc.).
            lang: Optional language code for filtering (primarily for API).
            output_filename: Optional filename for saving posts. If not provided, posts won't be saved immediately.
            max_pages: Maximum number of pages to fetch. Overrides config.API_MAX_PAGES if provided.
            start_page: Starting page number for scraping (default: 1).
            end_page: Ending page number for scraping. If not provided, scrapes until max_pages or end of content.

        Raises:
            ValueError: If the base_url is invalid.
        """
        self.base_url = self._validate_and_normalize_url(base_url)
        self.lang = lang
        self.output_filename = output_filename
        self.one_file = False  # Default to saving as separate files

        # Pagination control
        self.max_pages = max_pages if max_pages is not None else config.API_MAX_PAGES
        self.start_page = start_page
        self.end_page = end_page if end_page is not None else self.max_pages

        # Internal state
        self.discovered_urls: Set[str] = set()
        self.processed_urls: Set[str] = set()
        self.all_post_data: List[PostData] = []
        self.likely_post_url_pattern: Optional[str] = None
        self.filtered_urls: Set[str] = set()  # URLs that match the likely post pattern

        # Pagination state for special cases
        self._afry_pagination_template: Optional[str] = None
        self._afry_highest_page: int = 0
        self._afry_consecutive_empty_pages: int = 0
        self._afry_page_content_hashes: Dict[int, int] = {}  # Store content hash for each page
        self._afry_consecutive_duplicate_pages: int = 0

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

    def _save_post_to_file(self, post_data: PostData, index: int) -> None:
        """
        Saves a single post to a file immediately after processing.

        Args:
            post_data: The PostData object to save.
            index: The index of the post (used for filename generation).
        """
        if not self.output_filename:
            return  # Skip saving if no output filename was provided

        if self.one_file:
            # Append to a single file
            with open(self.output_filename, 'a', encoding='utf-8') as f:
                f.write(post_data.format_output())
            logger.info(f"Appended post to {self.output_filename}")
        else:
            # Create directory for separate files if it doesn't exist
            dir_name = self.output_filename
            if not os.path.exists(dir_name):
                os.makedirs(dir_name)
                logger.info(f"Created directory: {dir_name}")

            # Create a safe filename from the post title or use index if title is not available
            if post_data.title:
                # Sanitize title for filename
                safe_title = re.sub(r'[^\w\-.]+', '_', post_data.title).strip('_')
                # Limit filename length and ensure uniqueness with index
                safe_title = safe_title[:50] + f"_{index+1}"
            else:
                safe_title = f"post_{index+1}"

            file_path = os.path.join(dir_name, f"{safe_title}.txt")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(post_data.format_output())
            logger.info(f"Saved post to {file_path}")

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
        page = self.start_page
        max_pages = self.max_pages
        end_page = self.end_page

        while page <= end_page:
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

            if page > end_page:
                 logger.warning(f"Reached maximum API page limit ({end_page}).")
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

            # 4. Special case for AFRY: Handle both "/insight/" and "/insights/" paths
            if '/en/insight/' in parsed_url.path or '/en/insights/' in parsed_url.path:
                # This is likely a blog post on AFRY
                return True

            # 5. Path should generally be longer than the root path found initially
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

    def _extract_article_links(self, soup: BeautifulSoup, page_url: str) -> List[str]:
        """
        Extract a set of article-like links using content-based heuristics.
        This method removes typical navigation sections and applies filters based on link text.
        """
        # Remove known navigation sections to reduce noise
        for container in soup.find_all(['nav', 'header', 'footer']):
            container.decompose()

        # Gather links and deduplicate
        links = set()
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            full_url = urljoin(page_url, href)

            # Skip links that are empty or anchor-only
            if not full_url or full_url.startswith('#'):
                continue

            # Skip if link text is too short (unless it includes an image with alt text)
            link_text = a_tag.get_text(strip=True)
            if len(link_text) < 5 and not (a_tag.find('img') and a_tag.find('img').get('alt')):
                continue

            # Apply our existing URL-based heuristics
            if self._is_likely_post_url(href, page_url):
                links.add(full_url)

        return list(links)

    def _extract_non_wp_article_links(self, soup: BeautifulSoup, page_url: str) -> List[str]:
        """
        Extract a set of article-like links using a generic heuristic that doesn't rely on URL patterns.
        This is useful for non-WordPress sites where our URL-based heuristics might be too restrictive.

        This heuristic:
        1. Removes typical navigation sections (nav, header, footer)
        2. Extracts links with substantial text (>5 characters) or with images that have alt text
        3. Does NOT apply URL-based filtering, making it more suitable for sites with non-standard URL structures

        While this approach may include some non-article links (like social media sharing links),
        it generally finds more valid article links on non-WordPress sites compared to the WordPress-specific heuristic.
        """
        # Remove known navigation sections to reduce noise
        for container in soup.find_all(['nav', 'header', 'footer']):
            container.decompose()

        # Gather links and deduplicate
        links = set()
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            full_url = urljoin(page_url, href)

            # Skip links that are empty or anchor-only
            if not full_url or full_url.startswith('#'):
                continue

            # Skip if link text is too short (unless it includes an image with alt text)
            link_text = a_tag.get_text(strip=True)
            if len(link_text) < 5 and not (a_tag.find('img') and a_tag.find('img').get('alt')):
                continue

            # Add the link without applying URL-based heuristics
            links.add(full_url)

        return list(links)

    def _find_post_links_on_page(self, soup: BeautifulSoup, page_url: str, use_wp_heuristics: bool = True) -> None:
        """
        Scans a page's soup for likely post links and adds them to discovered_urls.

        Args:
            soup: The BeautifulSoup object of the page
            page_url: The URL of the page
            use_wp_heuristics: Whether to use WordPress-specific URL heuristics (default: True)
        """
        # Choose the appropriate link extraction method based on whether we're dealing with a WordPress site
        if use_wp_heuristics:
            article_links = self._extract_article_links(soup, page_url)
            logger.debug("Using WordPress-specific URL heuristics for link discovery")
        else:
            article_links = self._extract_non_wp_article_links(soup, page_url)
            logger.debug("Using generic heuristics for non-WordPress link discovery")

        # Add new links to discovered_urls
        found_count = 0
        for url in article_links:
            if url not in self.discovered_urls and url not in self.processed_urls:
                self.discovered_urls.add(url)
                found_count += 1
                logger.debug(f"Found potential post link: {url}")

        logger.info(f"Added {found_count} new potential post URLs from {page_url}")

    def _extract_pagination_links(self, soup: BeautifulSoup, page_url: str) -> List[str]:
        """
        Extract pagination links from a page.

        According to requirements, we only want URLs that:
        1. End with "?page=" followed by a number
        2. Don't have additional parameters after the page number
        3. Have page numbers between 1 and 100

        Args:
            soup: The BeautifulSoup object of the page
            page_url: The URL of the page

        Returns:
            A list of pagination URLs
        """
        pagination_links = []

        # Parse the base URL to get the part before any query parameters
        parsed_url = urlparse(page_url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"

        # Check if the current URL has a page parameter
        query_params = parse_qs(parsed_url.query)
        current_page = 0
        if 'page' in query_params and query_params['page']:
            try:
                current_page = int(query_params['page'][0])
            except ValueError:
                current_page = 0

        # Generate the next page URL if we're below page 100
        if current_page < 100:
            next_page = current_page + 1
            pagination_url = f"{base_url}?page={next_page}"
            pagination_links.append(pagination_url)
            logger.debug(f"Generated next pagination URL: {pagination_url}")

        # If we're on the base URL (no page parameter), also add page=1
        if current_page == 0 and 'page' not in query_params:
            pagination_url = f"{base_url}?page=1"
            pagination_links.append(pagination_url)
            logger.debug(f"Generated first pagination URL: {pagination_url}")

        return pagination_links

    def _scrape_html_for_links(self, use_wp_heuristics: bool = True) -> bool:
        """
        Scrapes the initial base_url for post links if API fails.
        Also navigates through pagination links to find more posts.

        Args:
            use_wp_heuristics: Whether to use WordPress-specific URL heuristics (default: True)
        """
        logger.info(f"Attempting to find post links via HTML scraping starting from {self.base_url}")

        # Start with the base URL
        pages_to_scrape = [self.base_url]
        scraped_pages = set()
        max_pages = self.max_pages
        start_page = self.start_page
        end_page = self.end_page

        initial_count = len(self.discovered_urls)

        # We don't need special case handling anymore as we're using a simpler pagination approach
        # that works for all sites

        # Process pages until we run out or hit the limit
        # Skip pages before start_page if possible (for HTML scraping, this is approximate)
        page_count = 1
        while pages_to_scrape and page_count <= end_page:
            current_page_url = pages_to_scrape.pop(0)

            # Skip if we've already scraped this page
            if current_page_url in scraped_pages:
                continue

            logger.info(f"Scraping page: {current_page_url}")

            # Store the count of discovered URLs before processing this page
            # (for detecting if new links were found)
            initial_discovered_count = len(self.discovered_urls)

            soup = self._fetch_soup(current_page_url)
            if not soup:
                logger.warning(f"Could not fetch or parse: {current_page_url}. Skipping.")
                continue

            # Only find post links if we're at or past the start_page
            if page_count >= start_page:
                # Find post links on this page
                self._find_post_links_on_page(soup, current_page_url, use_wp_heuristics)

                # Calculate how many new links were found on this page
                new_links_found = len(self.discovered_urls) - initial_discovered_count
                logger.debug(f"Found {new_links_found} new links on page: {current_page_url}")
            else:
                logger.debug(f"Skipping post link discovery for page {page_count} (before start_page {start_page})")

            # Mark this page as scraped
            scraped_pages.add(current_page_url)

            # Only process the page if we're at or past the start_page
            if page_count >= start_page:
                # Find pagination links and add them to the queue
                pagination_links = self._extract_pagination_links(soup, current_page_url)
                for link in pagination_links:
                    if link not in scraped_pages and link not in pages_to_scrape:
                        pages_to_scrape.append(link)
                        logger.debug(f"Added pagination link to queue: {link}")

            # Increment page counter
            page_count += 1

            # We don't need special case handling anymore as we're using a simpler pagination approach
            # that works for all sites

            # Be polite between page requests
            time.sleep(config.INTER_REQUEST_DELAY)

        if page_count > end_page:
            logger.warning(f"Reached maximum page limit ({end_page}). Some pages may not have been scraped.")

        logger.info(f"Scraped {len(scraped_pages)} pages in total.")
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

    def _analyze_url_patterns(self) -> None:
        """
        Analyzes discovered URLs to identify the most likely blog post URL pattern.
        This helps filter out non-blog-post URLs that might have been discovered.
        """
        if not self.discovered_urls:
            logger.warning("No URLs to analyze for patterns.")
            return

        # Extract path patterns from URLs
        path_patterns = {}
        for url in self.discovered_urls:
            parsed = urlparse(url)
            path = parsed.path

            # Skip URLs with query parameters or fragments for pattern analysis
            if parsed.query or parsed.fragment:
                continue

            # Count occurrences of each path pattern
            # We'll look at the directory structure and count how many URLs share similar patterns
            path_parts = path.split('/')
            if len(path_parts) >= 3:  # Need at least /dir/something
                # Create a pattern by keeping the directory structure but replacing the last part with a wildcard
                pattern = '/'.join(path_parts[:-1]) + '/*'
                path_patterns[pattern] = path_patterns.get(pattern, 0) + 1

        # Find the most common pattern
        most_common_pattern = None
        max_count = 0
        for pattern, count in path_patterns.items():
            if count > max_count:
                max_count = count
                most_common_pattern = pattern

        if most_common_pattern and max_count >= 3:  # Require at least 3 URLs with the same pattern
            self.likely_post_url_pattern = most_common_pattern
            logger.info(f"Identified likely blog post URL pattern: {most_common_pattern} (matched {max_count} URLs)")

            # Filter URLs based on the identified pattern
            for url in self.discovered_urls:
                parsed = urlparse(url)
                path = parsed.path
                path_parts = path.split('/')
                if len(path_parts) >= 3:
                    # Check if this URL matches the pattern (same directory structure)
                    url_pattern = '/'.join(path_parts[:-1]) + '/*'
                    if url_pattern == self.likely_post_url_pattern:
                        self.filtered_urls.add(url)

            logger.info(f"Filtered {len(self.filtered_urls)} URLs that match the likely blog post pattern out of {len(self.discovered_urls)} total discovered URLs")
        else:
            logger.warning("Could not identify a clear blog post URL pattern. Using all discovered URLs.")
            self.filtered_urls = self.discovered_urls.copy()

    def _fetch_and_extract_posts(self) -> None:
        """Iterates through discovered URLs, fetches content, and extracts data."""
        if not self.discovered_urls:
             logger.warning("No potential post URLs were discovered. Cannot extract posts.")
             return

        # Analyze URL patterns to identify the most likely blog post URLs
        self._analyze_url_patterns()

        # Use filtered URLs if available, otherwise use all discovered URLs
        urls_to_process = self.filtered_urls if self.filtered_urls else self.discovered_urls

        # Use the first URL to guess selectors if not already done (e.g., by API)
        if not self._api_used_successfully and not any(self.content_selectors.values()) and urls_to_process:
            sample_url = next(iter(urls_to_process)) # Get an arbitrary URL from the set
            self._guess_content_selectors(sample_url)

        logger.info(f"Fetching content for {len(urls_to_process)} URLs...")
        for url in list(urls_to_process): # Iterate over a copy for safe removal
            if url in self.processed_urls:
                continue

            logger.info(f"Processing URL: {url}")
            soup = self._fetch_soup(url)
            if soup:
                post_data = self._extract_post_data(url, soup)
                if post_data:
                    self.all_post_data.append(post_data)
                    # Save post immediately after processing
                    self._save_post_to_file(post_data, len(self.all_post_data) - 1)
            else:
                 logger.warning(f"Skipping post data extraction for {url} due to fetch/parse error.")

            self.processed_urls.add(url)
            # Be polite between fetching full post pages
            time.sleep(config.INTER_REQUEST_DELAY)

    def run(self) -> List[PostData]:
        """
        Executes the full scraping process.

        The scraper follows this workflow:
        1. Attempts to discover a WordPress REST API
        2. If API is found, fetches post URLs from the API
        3. If API is not found or fails:
           - Falls back to HTML link discovery
           - Uses a generic heuristic for non-WordPress sites that doesn't rely on URL patterns
           - This heuristic is more effective for sites with non-standard URL structures
        4. Fetches and extracts content from the discovered URLs

        Returns:
            A list of PostData objects containing the extracted blog posts
        """
        self._discover_wp_api()
        if self.api_root_url:
            self._fetch_urls_from_api() # Populates self.discovered_urls if successful

        if not self._api_used_successfully:
            logger.info("API not found or failed, falling back to HTML link discovery.")
            # Use generic heuristic for non-WordPress sites
            self._scrape_html_for_links(use_wp_heuristics=False) # Adds to self.discovered_urls

        self._fetch_and_extract_posts() # Fetches content and extracts data

        return self.all_post_data 
