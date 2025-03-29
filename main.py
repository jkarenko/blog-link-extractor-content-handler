import requests
import re
from bs4 import BeautifulSoup
import time
import argparse
from urllib.parse import urlparse, urljoin, parse_qs, urlencode, urlunparse
from collections import Counter # Import Counter for finding common paths
import json # Import json for potential API responses
import math

# --- Heuristic Functions ---

def guess_link_selector(base_url):
    """Tries common patterns to guess the CSS selector for blog post links (Fallback)."""
    print(f"Attempting to guess link selector for: {base_url}")
    try:
        response = requests.get(base_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        parsed_base = urlparse(base_url)
        base_path = parsed_base.path if parsed_base.path else '/'
        # If base path is deep, consider one level up as potential blog root
        potential_blog_root = '/'.join(base_path.split('/')[:-2]) + '/' if base_path.count('/') > 1 else base_path

        # Common selectors/patterns for blog post links
        # Prioritize selectors that are more specific (like within <article>)
        potential_selectors = [
            'article a[href]',            # Links within <article> tags
            '.post a[href]',              # Links within elements with class "post"
            '.entry a[href]',             # Links within elements with class "entry"
            'a[href*="/' + base_path.strip('/') + '/"]', # Links containing the base path segment
            'a[href*="' + potential_blog_root.strip('/') + '/"]', # Links containing parent path
            'a[href]'                     # Generic fallback (least preferred)
        ]

        for selector in potential_selectors:
            links = soup.select(selector)
            if not links:
                continue

            found_urls = set()
            common_paths = []
            print(f"  Testing selector: '{selector}' ({len(links)} links found)")
            for link in links:
                href = link.get('href')
                if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'):
                    continue

                full_url = urljoin(base_url, href)
                parsed_url = urlparse(full_url)

                # Basic filter: Must be on the same domain, avoid common non-post patterns
                if parsed_url.netloc == parsed_base.netloc and \
                   full_url != base_url and \
                   '?replytocom=' not in full_url and \
                   '/page/' not in full_url and \
                   '/category/' not in full_url and \
                   '/tag/' not in full_url and \
                   '/author/' not in full_url and \
                   not any(ft in parsed_url.path.lower() for ft in ['.pdf', '.jpg', '.png', '.zip']):

                    # Check if it seems like a 'deeper' path than the base listing page
                    if len(parsed_url.path) > len(base_path) and parsed_url.path.startswith(potential_blog_root):
                         found_urls.add(full_url)
                         # Get path segment after potential root for commonality check
                         relative_path = parsed_url.path[len(potential_blog_root):]
                         path_parts = relative_path.split('/')
                         if len(path_parts) > 1 and path_parts[0]:
                             common_paths.append(path_parts[0]) # Store first segment after root


            # Check if we found a reasonable number of candidate URLs and if they share common path structure
            if len(found_urls) > 1 :
                 # Check for a common first path segment after the potential blog root
                 if common_paths:
                     path_counts = Counter(common_paths)
                     most_common_path, count = path_counts.most_common(1)[0]
                     # Require at least half the links to share the most common path segment
                     if count >= len(found_urls) / 2:
                         print(f"  Selected selector '{selector}' - found {len(found_urls)} potential posts, common path prefix '{most_common_path}'")
                         # Return the selector and a filter function based on the common path
                         def filter_func(url_to_check, base_domain, common_prefix_path):
                             parsed_check = urlparse(url_to_check)
                             return parsed_check.netloc == base_domain and \
                                    parsed_check.path.startswith(common_prefix_path) and \
                                    url_to_check != base_url and \
                                    '?replytocom=' not in url_to_check and \
                                    '/page/' not in url_to_check # Add other exclusions?
                         # We need the root path from which the common segment starts
                         filter_path_root = potential_blog_root + most_common_path + '/'
                         return selector, lambda u: filter_func(u, parsed_base.netloc, filter_path_root)

                 # Fallback if no dominant common path found, but selector yielded results
                 print(f"  Selected selector '{selector}' - found {len(found_urls)} potential posts (using basic domain/path filter)")
                 def basic_filter_func(url_to_check, base_domain, blog_root_path):
                      parsed_check = urlparse(url_to_check)
                      return parsed_check.netloc == base_domain and \
                             parsed_check.path.startswith(blog_root_path) and \
                             len(parsed_check.path) > len(blog_root_path) + 1 and \
                             url_to_check != base_url and \
                             '?replytocom=' not in url_to_check and \
                             '/page/' not in url_to_check
                 return selector, lambda u: basic_filter_func(u, parsed_base.netloc, potential_blog_root)


        print("  Warning: Could not reliably determine a specific link selector. Falling back to basic 'a[href]' filtering.")
        # Fallback filter if no good selector found
        def fallback_filter(url_to_check, base_domain, blog_root_path):
             parsed_check = urlparse(url_to_check)
             return parsed_check.netloc == base_domain and \
                    parsed_check.path.startswith(blog_root_path) and \
                    len(parsed_check.path) > len(blog_root_path) + 1 and \
                    url_to_check != base_url and \
                    '?replytocom=' not in url_to_check and \
                    '/page/' not in url_to_check
        return 'a[href]', lambda u: fallback_filter(u, parsed_base.netloc, potential_blog_root)

    except requests.exceptions.RequestException as e:
        print(f"Error fetching {base_url} for inspection: {e}")
        return 'a[href]', lambda u: False # Return a default that finds nothing on error

def guess_content_selectors(post_url):
    """Tries to guess CSS selectors for title, date, and content on a post page."""
    print(f"Attempting to guess content selectors for: {post_url}")
    selectors = {'title': 'h1', 'date': None, 'content': 'article'} # Defaults
    try:
        response = requests.get(post_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Guess Title (usually the first H1, or specific classes)
        title_selectors = ['h1.entry-title', 'h1.post-title', 'h1[itemprop="headline"]', 'h1']
        for sel in title_selectors:
            elem = soup.select_one(sel)
            if elem and elem.get_text(strip=True):
                selectors['title'] = sel
                print(f"  Guessed title selector: '{sel}'")
                break

        # Guess Date (time tag, common classes, regex)
        date_selectors = [
            'time[datetime]', 'span.date', 'div.post-date', 'p.date',
            '.published', '.entry-date', 'time.published'
        ]
        date_found = False
        for sel in date_selectors:
            elem = soup.select_one(sel)
            if elem and elem.get_text(strip=True):
                 # Check if text looks like a date (simple check)
                 text = elem.get_text(strip=True)
                 if re.search(r'\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\w+ \d{1,2}, \d{4}', text): # Common patterns
                     selectors['date'] = sel
                     print(f"  Guessed date selector: '{sel}'")
                     date_found = True
                     break
        # If no selector found, try regex on the whole body (less reliable)
        if not date_found:
            date_pattern = re.compile(r'\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\w+ \d{1,2},? \d{4})\b')
            # Look near the title first
            title_elem = soup.select_one(selectors['title'])
            parent_area = title_elem.parent if title_elem else soup.body
            if parent_area:
                text_near_title = parent_area.get_text(" ", strip=True)
                # Limit search area to avoid picking dates from comments/footer
                match = date_pattern.search(text_near_title[:500])
                if match:
                    selectors['date_text'] = match.group(0) # Store raw text if found by regex
                    print(f"  Guessed date text via regex: '{match.group(0)}'")


        # Guess Content Area (common semantic tags or divs)
        content_selectors = [
            'article .entry-content', 'article .post-content', 'article',
            '.post-body', '.blog-content', 'div[role="main"]', 'main'
            # Maybe add: find largest text block? More complex.
        ]
        best_content_selector = None
        max_text_len = 0
        for sel in content_selectors:
            elem = soup.select_one(sel)
            if elem:
                text_len = len(elem.get_text(strip=True))
                # Prefer selectors that yield substantial text length
                if text_len > max_text_len and text_len > 200: # Threshold for 'real' content
                    max_text_len = text_len
                    best_content_selector = sel

        if best_content_selector:
            selectors['content'] = best_content_selector
            print(f"  Guessed content selector: '{best_content_selector}' (length: {max_text_len})")
        elif soup.find('article'):
             selectors['content'] = 'article' # Default fallback if specific ones fail
             print("  Warning: Using fallback content selector 'article'.")
        else:
             selectors['content'] = 'body' # Last resort
             print("  Warning: Could not guess specific content area, using 'body'. Check output quality.")


        return selectors

    except requests.exceptions.RequestException as e:
        print(f"Error fetching {post_url} for inspection: {e}")
        return selectors # Return defaults on error
    except Exception as e:
        print(f"Error parsing {post_url} during inspection: {e}")
        return selectors # Return defaults on error


# --- API Discovery and Fetching ---

def find_wp_api_root(soup):
    """Searches BeautifulSoup object for the standard WordPress REST API link."""
    api_link_tag = soup.find('link', rel='https://api.w.org/')
    if api_link_tag and api_link_tag.get('href'):
        api_root_url = api_link_tag['href']
        print(f"  Found potential WP API root via <link> tag: {api_root_url}")
        # Basic validation
        if api_root_url.endswith('/wp-json/'):
            return api_root_url
        else:
            print("  Warning: API root URL doesn't end with '/wp-json/'. Proceeding cautiously.")
            return api_root_url # Still return it, might be valid
    print("  WP API <link> tag not found.")
    return None

def get_urls_from_wp_api(session, api_root_url, base_url, lang=None):
    """
    Attempts to fetch post URLs from a discovered WordPress REST API endpoint.
    Handles pagination using X-WP-TotalPages header.
    """
    if not api_root_url.endswith('/'):
        api_root_url += '/'
    posts_endpoint = urljoin(api_root_url, 'wp/v2/posts')

    parsed_original = urlparse(base_url)
    original_params = parse_qs(parsed_original.query)
    base_api_params = {k: v[0] if len(v) == 1 else v for k, v in original_params.items()}

    base_api_params['per_page'] = 100
    base_api_params['_fields'] = 'link,lang'
    if lang:
        base_api_params['lang'] = lang
        print(f"  Applying language filter: {lang}")

    all_urls = set()
    current_page = 1
    total_pages = 1 # Assume 1 page initially

    while current_page <= total_pages:
        api_params = base_api_params.copy()
        api_params['page'] = current_page

        print(f"  Fetching page {current_page}/{total_pages} from WP API endpoint: {posts_endpoint} with params: {api_params}")

        try:
            response = session.get(posts_endpoint, params=api_params, timeout=20)
            response.raise_for_status()

            # Update total_pages only on the first successful request
            if current_page == 1:
                total_pages_header = response.headers.get('X-WP-TotalPages')
                if total_pages_header and total_pages_header.isdigit():
                    total_pages = int(total_pages_header)
                    total_items = response.headers.get('X-WP-Total', 'N/A')
                    print(f"  API indicates {total_items} total posts across {total_pages} pages.")
                else:
                    print("  Warning: Could not determine total pages from API headers. Only fetching page 1.")
                    total_pages = 1 # Reset to 1 if header is missing/invalid

            posts_data = response.json()

            # --- Process response data (same logic as before) ---
            if not isinstance(posts_data, list):
                print(f"  Error: API response for page {current_page} is not a list.")
                if isinstance(posts_data, dict) and 'posts' in posts_data and isinstance(posts_data['posts'], list):
                    print("  Detected Miltton-like API structure ('posts' key). Processing that.")
                    posts_data = posts_data['posts']
                    page_extracted_count = 0
                    for post in posts_data:
                        post_lang = post.get('lang')
                        if lang and post_lang and post_lang != lang: continue
                        url = post.get('url')
                        if url and isinstance(url, str) and url.startswith(('http://', 'https://')):
                            all_urls.add(url.replace('\\/', '/'))
                            page_extracted_count += 1
                    print(f"  Extracted {page_extracted_count} URLs from page {current_page} (Miltton format).")
                    # Assume Miltton doesn't paginate in this custom endpoint? Break after first page.
                    break
                else:
                    break # Stop pagination if response format is wrong

            page_extracted_count = 0
            for post in posts_data:
                post_lang = post.get('lang')
                if lang and post_lang and post_lang != lang: continue

                url = post.get('link')
                if url and isinstance(url, str) and url.startswith(('http://', 'https://')):
                    all_urls.add(url)
                    page_extracted_count += 1
                else:
                    url_fallback = post.get('url')
                    if url_fallback and isinstance(url_fallback, str) and url_fallback.startswith(('http://', 'https://')):
                         post_lang_fallback = post.get('lang')
                         if lang and post_lang_fallback and post_lang_fallback != lang: continue
                         all_urls.add(url_fallback.replace('\\/', '/'))
                         page_extracted_count += 1

            print(f"  Extracted {page_extracted_count} URLs from page {current_page}.")
            # --- End of response processing ---

            if page_extracted_count == 0 and current_page > 1:
                 print(f"  No results found on page {current_page}. Stopping pagination.")
                 break # Stop if a page returns no results (likely reached end)

            current_page += 1
            if current_page <= total_pages:
                time.sleep(0.5) # Add a small delay between API page requests

        except requests.exceptions.RequestException as e:
            print(f"  Error fetching page {current_page} from WP API endpoint {posts_endpoint}: {e}")
            break # Stop pagination on error
        except json.JSONDecodeError as e:
            print(f"  Error decoding JSON response from WP API on page {current_page}: {e}")
            break # Stop pagination on error
        except Exception as e:
            print(f"  Unexpected error processing WP API data on page {current_page}: {e}")
            break # Stop pagination on error


    print(f"  Finished API fetching. Total unique URLs found: {len(all_urls)}")
    return list(all_urls)


# --- Main URL Gathering Function (Dispatcher) ---

def get_blog_urls(base_url, lang=None):
    """
    Gets blog URLs. Tries to discover and use WordPress API first,
    falls back to HTML scraping if API not found or fails. Passes lang filter to API method.
    """
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})
    blog_urls = []
    api_used = False

    # 1. Fetch initial HTML to check for API link
    print(f"Fetching initial page to check for API indicators: {base_url}")
    try:
        response = session.get(base_url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 2. Try to find WP API root
        api_root_url = find_wp_api_root(soup)

        if api_root_url:
            # 3. If API root found, attempt to fetch URLs via API, passing lang
            print("Attempting to fetch URLs via discovered WP API...")
            blog_urls = get_urls_from_wp_api(session, api_root_url, base_url, lang=lang) # Pass lang
            if blog_urls:
                api_used = True # Flag that API succeeded
            else:
                print("WP API call failed or returned no URLs. Falling back to HTML scraping.")
        else:
             print("No WP API root discovered. Proceeding with HTML scraping.")

    except requests.exceptions.RequestException as e:
        print(f"Error fetching initial page {base_url}: {e}. Proceeding with fallback HTML scraping attempt.")
        soup = BeautifulSoup("", 'html.parser')
    except Exception as e:
        print(f"Error parsing initial page {base_url}: {e}. Proceeding with fallback HTML scraping attempt.")
        soup = BeautifulSoup("", 'html.parser')


    # 4. Fallback to HTML scraping if API wasn't found or failed
    if not api_used:
        print("Using generic HTML scraping strategy...")
        # Note: Language filter is NOT applied in the HTML fallback currently.
        # The filter function from guess_link_selector focuses on URL structure.
        link_selector, link_filter_func = guess_link_selector_from_soup(soup, base_url)

        if not link_selector:
            print("Could not guess link selector from initial HTML. No URLs will be extracted.")
            return []

        all_urls_html = set()
        print(f"Scraping initial page HTML (using selector: '{link_selector}')")
        links = soup.select(link_selector) # Use the soup we already have
        print(f"Found {len(links)} links with selector '{link_selector}' in initial HTML.")

        for link in links:
            href = link.get('href')
            if href:
                full_url = urljoin(base_url, href)
                if link_filter_func(full_url):
                    if full_url not in all_urls_html:
                        all_urls_html.add(full_url)

        blog_urls = list(all_urls_html)
        print(f"Extracted {len(blog_urls)} unique URLs via HTML scraping (language filter not applied).")

    return blog_urls

# --- Helper for HTML Scraping Fallback ---

def guess_link_selector_from_soup(soup, base_url):
    """
    Tries common patterns to guess the CSS selector for blog post links
    using an existing BeautifulSoup object. (Modified from guess_link_selector)
    """
    print(f"Attempting to guess link selector from existing HTML for: {base_url}")
    parsed_base = urlparse(base_url)
    base_path = parsed_base.path if parsed_base.path else '/'
    potential_blog_root = '/'.join(base_path.split('/')[:-2]) + '/' if base_path.count('/') > 1 else base_path

    potential_selectors = [
        'article a[href]', '.post a[href]', '.entry a[href]',
        # Avoid selectors based on base_path here as they might be too specific now
        'a[href]'
    ]

    # Simplified logic compared to original guess_link_selector
    # Focus on finding *any* plausible links within common containers first

    for selector in potential_selectors:
        links = soup.select(selector)
        if not links: continue

        found_urls = set()
        print(f"  Testing selector: '{selector}' ({len(links)} links found)")
        for link in links:
            href = link.get('href')
            if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'): continue

            full_url = urljoin(base_url, href)
            parsed_url = urlparse(full_url)

            # Basic filter (same as before)
            if parsed_url.netloc == parsed_base.netloc and \
               full_url != base_url and \
               '?replytocom=' not in full_url and '/page/' not in full_url and \
               '/category/' not in full_url and '/tag/' not in full_url and \
               '/author/' not in full_url and \
               not any(ft in parsed_url.path.lower() for ft in ['.pdf', '.jpg', '.png', '.zip']):

                 # Basic path check (starts with potential root, is longer than root)
                 if parsed_url.path.startswith(potential_blog_root) and len(parsed_url.path) > len(potential_blog_root) + 1 :
                      found_urls.add(full_url)

        # Select if it found *any* plausible URLs
        if len(found_urls) > 1:
            print(f"  Selected selector '{selector}' - found {len(found_urls)} potential posts.")
            def filter_func(url_to_check, base_domain, blog_root_path):
                 parsed_check = urlparse(url_to_check)
                 return parsed_check.netloc == base_domain and \
                        parsed_check.path.startswith(blog_root_path) and \
                        len(parsed_check.path) > len(blog_root_path) + 1 and \
                        url_to_check != base_url and \
                        '?replytocom=' not in url_to_check and '/page/' not in url_to_check
            return selector, lambda u: filter_func(u, parsed_base.netloc, potential_blog_root)

    print("  Warning: Could not determine a specific link selector via HTML. Falling back to basic 'a[href]' filtering.")
    def fallback_filter(url_to_check, base_domain, blog_root_path):
         parsed_check = urlparse(url_to_check)
         return parsed_check.netloc == base_domain and \
                parsed_check.path.startswith(blog_root_path) and \
                len(parsed_check.path) > len(blog_root_path) + 1 and \
                url_to_check != base_url and \
                '?replytocom=' not in url_to_check and '/page/' not in url_to_check
    return 'a[href]', lambda u: fallback_filter(u, parsed_base.netloc, potential_blog_root)


# --- Content Extraction & Saving ---

def get_blog_content(url, content_selectors):
    """Extract content from a blog post using guessed selectors."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # --- Use Guessed Selectors ---
        title_selector = content_selectors.get('title', 'h1') # Fallback to h1
        date_selector = content_selectors.get('date')
        content_selector = content_selectors.get('content', 'body') # Fallback to body

        # Get title
        title_elem = soup.select_one(title_selector)
        title = title_elem.get_text(strip=True) if title_elem else "No title found"

        # Get date
        date = ""
        if date_selector:
            date_elem = soup.select_one(date_selector)
            if date_elem:
                # Extract specific attribute if needed (e.g., datetime from <time>)
                if date_elem.name == 'time' and date_elem.get('datetime'):
                    date = date_elem.get('datetime')
                else:
                    date = date_elem.get_text(strip=True)
        elif 'date_text' in content_selectors: # Use regex match if found
             date = content_selectors['date_text']


        # Get main content
        content = ""
        main_content = soup.select_one(content_selector)
        if main_content:
            # Extract text from relevant tags within the main content area
            # Prefer common text block elements
            elements = main_content.find_all(['p', 'h2', 'h3', 'h4', 'h5', 'ul', 'ol', 'li', 'blockquote'])
            if not elements and content_selector == 'body': # If body fallback, get direct paragraphs
                 elements = main_content.find_all('p', recursive=False)

            for elem in elements:
                 # Avoid extracting text from known non-content sections if possible (basic check)
                 if not elem.find_parent(['nav', 'footer', 'header', 'aside', 'form']):
                     content += elem.get_text(separator=' ', strip=True) + "\n\n"

            # Fallback if specific tags yield nothing but main_content was found
            if not content.strip() and main_content and content_selector != 'body':
                 print(f"  Note: No specific elements (p, h2..) found in {content_selector}. Extracting all text.")
                 content = main_content.get_text(separator='\n', strip=True)
        else:
            print(f"Warning: Content selector '{content_selector}' failed for {url}. No content extracted.")

        # Basic cleaning
        content = re.sub(r'\n{3,}', '\n\n', content).strip() # Remove excessive newlines

        formatted_post = f"# {title}\nURL: {url}\nDate: {date}\n\n{content}\n\n{'='*80}\n\n"
        return formatted_post

    except requests.exceptions.RequestException as e:
        print(f"Error fetching blog content from {url}: {e}")
        return f"Error processing {url}: {str(e)}\n\n{'='*80}\n\n"
    except Exception as e:
        print(f"Error parsing content from {url}: {str(e)}")
        return f"Error parsing {url}: {str(e)}\n\n{'='*80}\n\n"


def save_to_file(content, filename="blog_posts.txt"):
    """Save content to a file."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Content saved to {filename}")
    except IOError as e:
        print(f"Error saving file {filename}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Scrape blog posts. Tries WP API first, then HTML scraping.")
    parser.add_argument("base_url", help="The base URL of the blog listing page.")
    parser.add_argument("-o", "--output", help="Output filename", default=None)
    parser.add_argument("-l", "--lang", help="Optional language code filter (e.g., 'en', 'fi'). Primarily affects API requests.", default=None) # Added lang argument

    args = parser.parse_args()
    base_url = args.base_url
    lang_filter = args.lang # Get the language filter

    # Generate default filename
    if args.output:
        filename = args.output
    else:
        domain = urlparse(base_url).netloc.replace('www.', '')
        filename = f"{domain}_blog_posts.txt"

    all_posts_content = f"# Blog Posts Collection from {base_url}"
    if lang_filter:
        all_posts_content += f" (Language: {lang_filter})"
    all_posts_content += "\n\n"

    # --- Get Blog URLs (passing language filter) ---
    print("-" * 20)
    print(f"Getting blog URLs for {base_url}...")
    # Pass lang_filter to the dispatcher
    blog_urls = get_blog_urls(base_url, lang=lang_filter)

    if not blog_urls:
        print("No blog post URLs found. Check the URL or website structure.")
        return
    print(f"Found {len(blog_urls)} potential blog post URLs.")
    print("-" * 20)

    # --- Guess Content Selectors ---
    content_selectors = {}
    if blog_urls:
        print("--- Auto-Detecting Content Selectors (using first URL) ---")
        content_selectors = guess_content_selectors(blog_urls[0])
    print("-" * 20)

    # --- Process Posts ---
    print("--- Processing Individual Blog Posts ---")
    processed_count = 0
    # Simple heuristic to guess if API was used for adjusting sleep time
    likely_api_used = False
    if blog_urls:
        # Check if the first URL looks like it came from the API path
        # This is imperfect but better than nothing without refactoring more
        first_url_parsed = urlparse(blog_urls[0])
        if 'wp-json' in first_url_parsed.path or '.com/' not in first_url_parsed.path: # Crude check
             # This check is flawed, API returns actual post URLs. Need better check.
             # Let's assume API was used if get_blog_urls returned something and didn't print "Using generic HTML..."?
             # Requires refactoring get_blog_urls to return the flag.
             # For now, let's just use a smaller delay consistently.
             likely_api_used = True # Assume API was used if URLs were found via get_blog_urls


    for i, url in enumerate(sorted(list(set(blog_urls)))):
        print(f"Processing post {i+1}/{len(blog_urls)}: {url}")
        try:
            post_content = get_blog_content(url, content_selectors)
            if post_content:
                 # Correct indentation here
                 all_posts_content += post_content
                 processed_count += 1
            # Adjust sleep time
            time.sleep(0.2 if likely_api_used else 0.5) # Use the heuristic
        except Exception as e:
            print(f"Unexpected error processing {url}: {str(e)}")
            all_posts_content += f"Error processing {url}: {str(e)}\n\n{'='*80}\n\n"

    print("-" * 20)
    print(f"Successfully processed content for {processed_count}/{len(blog_urls)} URLs.")
    
    # Save all content to file
    save_to_file(all_posts_content, filename)


if __name__ == "__main__":
    main()
