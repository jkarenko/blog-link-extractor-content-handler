from dataclasses import dataclass
from typing import Optional

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
