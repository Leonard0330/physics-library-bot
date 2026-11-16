from .ads import AdsProvider
from .arxiv import ArxivProvider
from .hal import HalProvider
from .zenodo import ZenodoProvider

# Add new providers here: name -> class. Manager and other providers stay untouched.
REGISTRY = {p.name: p for p in (ArxivProvider, ZenodoProvider, HalProvider, AdsProvider)}
