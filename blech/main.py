import argparse
import importlib.metadata  # To read version from pyproject.toml
import logging
import os
import re
import sys
from typing import List
from urllib.parse import urlparse

import requests

# Import config_defaults for default values
try:
    # When running as an installed package
    from . import config_defaults as config
except ImportError:
    # When running the file directly
    from blech import config_defaults as config

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
    parser.add_argument("--max-pages", type=int, help="Maximum number of pages to fetch.", default=config.API_MAX_PAGES)
    parser.add_argument("--start-page", type=int, help="Starting page number for scraping.", default=1)
    parser.add_argument("--end-page", type=int, help="Ending page number for scraping.", default=None)

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
        # Create scraper with output_filename parameter and pagination parameters
        scraper = BlogScraper(
            base_url=args.base_url, 
            lang=args.lang, 
            output_filename=output_filename,
            max_pages=args.max_pages,
            start_page=args.start_page,
            end_page=args.end_page
        )
        # Set one_file flag based on command-line argument
        scraper.one_file = args.one_file

        logging.info("Starting scraping process...")
        all_posts_data: List[PostData] = scraper.run()
        logging.info(f"Scraping finished. Found {len(all_posts_data)} posts.")

        # Posts are now saved immediately after processing, so we don't need to save them again here
        if all_posts_data:
            if args.one_file:
                logging.info(f"All posts have been saved to {output_filename}.")
            else:
                logging.info(f"All posts have been saved as separate files in {output_filename}.")
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
