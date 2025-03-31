import argparse
import importlib.metadata  # To read version from pyproject.toml
import logging
import os
import re
import sys
from typing import List
from urllib.parse import urlparse

import requests

try:
    __version__ = importlib.metadata.version("blech")
except importlib.metadata.PackageNotFoundError:
    # Handle case where package is not installed (e.g., running from source)
    __version__ = "0.0.0-dev"

# Import from local modules within the package
try:
    # When running as an installed package
    from .models import PostData
    from .scraper import BlogScraper
except ImportError:
    # When running the file directly
    from blech.models import PostData
    from blech.scraper import BlogScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define the main function that will be called by the entry point
def main():
    parser = argparse.ArgumentParser(
        description="Scrape blog posts. Tries WP REST API first, then falls back to HTML scraping heuristics.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("base_url", help="The base URL of the blog listing page (e.g., 'https://example.com/blog').")
    parser.add_argument(
        "-vsn", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("-o", "--output", help="Output filename (.txt). If not provided, generates based on domain.", default=None)
    parser.add_argument("-l", "--lang", help="Optional language code filter (e.g., 'en', 'fi'). Primarily affects API requests.", default=None)
    parser.add_argument("--one-file", help="Save all blog posts to a single file instead of separate files.", action="store_true")
    parser.add_argument("-v", "--verbose", help="Enable debug logging.", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        # Get the root logger and set level to DEBUG
        logging.getLogger().setLevel(logging.DEBUG)
        # Also need to adjust handler level if using basicConfig defaults
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
            output_filename = f"{safe_domain}_blog_posts{'.txt' if args.one_file else ''}" if safe_domain else "blog_posts_output.txt"
        except Exception as e:
            logging.warning(f"Could not parse domain from base_url: {e}. Using default filename.")
            output_filename = f"blog_posts_output.txt"
        logging.info(f"Output filename not specified, using default: {output_filename}")

    try:
        scraper = BlogScraper(base_url=args.base_url, lang=args.lang)
        logging.info("Starting scraping process...")
        all_posts_data: List[PostData] = scraper.run()
        logging.info(f"Scraping finished. Found {len(all_posts_data)} posts.")

        if all_posts_data:
            if args.one_file:
                logging.info(f"Saving posts to {output_filename}...")
                with open(output_filename, 'w', encoding='utf-8') as f:
                    for post_data in all_posts_data:
                        f.write(post_data.format_output())
                logging.info("Successfully saved posts.")
            else:
                # Create directory for separate files
                dir_name = output_filename
                if not os.path.exists(dir_name):
                    os.makedirs(dir_name)
                    logging.info(f"Created directory: {dir_name}")

                logging.info(f"Saving posts as separate files in {dir_name}...")
                for i, post_data in enumerate(all_posts_data):
                    # Create a safe filename from the post title or use index if title is not available
                    if post_data.title:
                        # Sanitize title for filename
                        safe_title = re.sub(r'[^\w\-.]+', '_', post_data.title).strip('_')
                        # Limit filename length and ensure uniqueness with index
                        safe_title = safe_title[:50] + f"_{i+1}"
                    else:
                        safe_title = f"post_{i+1}"

                    file_path = os.path.join(dir_name, f"{safe_title}.txt")
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(post_data.format_output())
                    logging.debug(f"Saved post to {file_path}")

                logging.info(f"Successfully saved {len(all_posts_data)} posts as separate files.")
        else:
            logging.warning("No posts were successfully extracted.")

    except ValueError as ve: # Specific configuration/setup errors
        logging.error(f"Configuration error: {ve}")
        sys.exit(1) # Exit with error code
    except ImportError as ie:
        logging.error(f"Import error: {ie}. Make sure all package components are correctly installed.")
        sys.exit(1) # Exit with error code
    except requests.exceptions.RequestException as req_exc:
         logging.error(f"Network error during scraping: {req_exc}")
         sys.exit(1) # Exit with error code
    except Exception as e:
        logging.error(f"An unexpected error occurred during scraping: {e}", exc_info=True)
        sys.exit(1) # Exit with error code

# This allows running the script directly (python blech/main.py) for development/testing
# The installed 'blech' command will call the main() function directly via the entry point
if __name__ == "__main__":
    main() 
