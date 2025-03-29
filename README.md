# Blog Link Extractor & Content Handler (BLECH)

BLECH is a tool designed to automatically identify and extract links to individual posts from a blog's main page, index, or feed. After identifying the links, it proceeds to fetch and parse the content of each blog post, making it easier to process, analyze, or archive blog data.

## Usage

```bash
blech [OPTIONS] <BASE_URL>
```

### Positional Arguments:

*   `<BASE_URL>`: (Required) The starting URL of the blog's main page, index, or feed where post links can be found.

### Options:

*   `-o`, `--output <FILENAME>`: (Optional) The file where extracted content should be saved. If not provided, a default filename will be generated based on the blog's domain (e.g., `example-blog.com_blog_posts.txt`).
*   `-l`, `--lang <LANG_CODE>`: (Optional) Filter posts by language code (e.g., 'en', 'fi'). This primarily works when the blog uses a WordPress REST API that supports language filtering.
*   `-h`, `--help`: (Optional) Show this help message and exit.

### Example:

```bash
# Scrape English posts from the blog archive and save to a specific file
blech --output my_blog_extract.txt --lang en https://example-blog.com/archive

# Scrape all posts and use the default filename
blech https://another-blog.org/
