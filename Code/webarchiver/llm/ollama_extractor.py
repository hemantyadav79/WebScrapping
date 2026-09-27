import json
import logging
import urllib.request
import urllib.error
from bs4 import BeautifulSoup
from webarchiver.listings.extractor import ListingExtractor
from webarchiver.listings.models import ListingRecord
from webarchiver.core.url_utils import normalize_url, resolve_url

_PROMPT = """You are a real estate data extraction assistant.
Given the text of a property listing page below, extract the property details into a flat JSON object.
Use consistent keys. Example keys you MIGHT use: Address, Price, Bedrooms, Bathrooms, Parking, Property Type, Agent, Agency, Land Size, Build Year, Description, etc.
If you find any other interesting features (like Balcony, Pool, etc.), include them as well!
If this text does NOT look like a property listing, return exactly {{}}.
Do NOT wrap the JSON in markdown or anything else. Just output raw JSON.

Text:
{text}
"""

class OllamaListingExtractor(ListingExtractor):
    name = "ollama"

    def __init__(self, host: str = "http://localhost:11434", model: str = "llama3.2:1b", timeout: int = 600, logger=None):
        self._host = host.rstrip("/")
        self._model = model
        self._timeout = max(1, timeout)
        self._log = logger or logging.getLogger("webarchiver.llm")

    def _generate_sync(self, prompt: str) -> str:
        payload = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return data.get("response", "")

    def extract(self, html: str, base_url: str) -> list[ListingRecord]:
        soup = BeautifulSoup(html or "", "lxml")
        
        # Only extract from body to avoid scripts/styles
        body = soup.body if soup.body else soup
        # Remove noisy tags to save tokens
        for tag in body(["script", "style", "noscript", "svg", "nav", "footer"]):
            tag.decompose()
            
        text = body.get_text(separator=" ", strip=True)
        # limit text length to avoid context overflow
        text = text[:8000]
        
        if not text:
            return []

        prompt = _PROMPT.format(text=text)
        
        try:
            raw = self._generate_sync(prompt)
            data = json.loads(raw)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self._log.warning("Ollama unreachable during extraction: %s", exc)
            return []
        except (json.JSONDecodeError, ValueError):
            self._log.debug("Ollama returned malformed JSON during extraction.")
            return []

        if not isinstance(data, dict) or not data:
            return []

        # It looks like a listing! Let's build the record.
        rec = ListingRecord(source_page=base_url, listing_url=base_url)
        
        # Fill standard fields if present, remove them from data so dynamic_data only has the rest
        # We'll do case-insensitive matching for standard fields
        data_lower = {k.lower(): (k, v) for k, v in data.items()}
        
        def pop_key(keys: list[str]) -> str:
            for k in keys:
                if k in data_lower:
                    orig_k, val = data_lower.pop(k)
                    data.pop(orig_k, None)
                    return str(val)
            return ""

        rec.address = pop_key(["address", "location", "property address"])
        rec.price = pop_key(["price", "cost", "value"])
        rec.bedrooms = pop_key(["bedrooms", "bed", "beds"])
        rec.bathrooms = pop_key(["bathrooms", "bath", "baths"])
        rec.parking = pop_key(["parking", "car", "cars", "car spaces"])
        rec.property_type = pop_key(["property type", "type", "category"])
        rec.agent = pop_key(["agent", "agent name", "contact"])
        rec.agency = pop_key(["agency", "agency name", "real estate agent"])
        
        rec.dynamic_data = {str(k): str(v) for k, v in data.items()}
        
        # Now get all images from the page
        seen_imgs = set()
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if not src:
                continue
            # Filter out obvious tiny tracking pixels or icons
            if "logo" in src.lower() or "icon" in src.lower():
                continue
            resolved = resolve_url(base_url, src)
            if resolved:
                norm = normalize_url(resolved)
                if norm not in seen_imgs:
                    seen_imgs.add(norm)
                    rec.image_urls.append(norm)

        # Some sites use picture > source or meta tags for hi-res images
        for meta in soup.find_all("meta", property="og:image"):
            content = meta.get("content")
            if content:
                resolved = resolve_url(base_url, content)
                if resolved:
                    norm = normalize_url(resolved)
                    if norm not in seen_imgs:
                        seen_imgs.add(norm)
                        rec.image_urls.append(norm)

        return [rec]
