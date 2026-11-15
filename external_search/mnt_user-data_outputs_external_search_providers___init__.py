from .arxiv import ArxivProvider

# Add new providers here: name -> class. Manager and other providers stay untouched.
REGISTRY = {ArxivProvider.name: ArxivProvider}
