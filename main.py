import argparse
import logging
import re
import time
from typing import List, Optional
from urllib.parse import urlparse

# Import from local modules
from models import PostData
from scraper import BlogScraper
# config is used implicitly by scraper, no direct import needed here unless accessing constants

# --- Basic Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


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
        # Get the root logger and set level to DEBUG
        logging.getLogger().setLevel(logging.DEBUG)
        # Also need to adjust handler level if using basicConfig defaults
        # For simplicity assuming basicConfig sets handler level too, or using a more complex logging setup
        for handler in logging.getLogger().handlers:
             handler.setLevel(logging.DEBUG)
        logging.debug("Debug logging enabled.")


    # Generate default filename if needed
    output_filename = args.output
    if not output_filename:
        try:
            domain = urlparse(args.base_url).netloc.replace('www.', '')
            # Sanitize domain for filename more robustly
            safe_domain = re.sub(r'[^\w\-.]+', '_', domain).strip('_')
            output_filename = f"{safe_domain}_blog_posts.txt" if safe_domain else "blog_posts_output.txt"
        except Exception as e:
            logging.warning(f"Could not parse domain from base_url: {e}. Using default filename.")
            output_filename = "blog_posts_output.txt"
        logging.info(f"Output filename not specified, using default: {output_filename}")
    # Ensure filename ends with .txt
    if not output_filename.lower().endswith('.txt'):
         output_filename += ".txt"
         logging.debug(f"Appended .txt extension. Filename is now: {output_filename}")


    try:
        # Instantiate the scraper from the scraper module
        scraper_instance = BlogScraper(base_url=args.base_url, lang=args.lang)
        # Run the scrape process
        extracted_posts = scraper_instance.scrape()

        if extracted_posts:
            # Save the results using the function in this module
            save_posts_to_file(extracted_posts, output_filename, args.base_url, args.lang)
        else:
            logging.info("No posts were extracted. Nothing to save.")

    except ValueError as ve: # Catch potential ValueError from BlogScraper init
         logging.error(f"Configuration error: {ve}")
    except ImportError as ie:
         logging.error(f"Import error: {ie}. Make sure config.py and models.py are in the same directory or Python path.")
    except Exception as e:
        logging.error(f"An unexpected error occurred during scraping: {e}", exc_info=True)

if __name__ == "__main__":
    main()
