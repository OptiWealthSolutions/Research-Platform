import requests
import feedparser
import hashlib
import re
from datetime import datetime
from dateutil import parser as date_parser

# Configuration
ELSEVIER_API_KEY = "f6a884c51312665bd93f3a3ea91e1f8c"
BIS_RSS_URL = "https://www.bis.org/doclist/workpap.rss"
FED_RSS_URL = "https://www.federalreserve.gov/feeds/staff_reports.xml"

class ResearchIngester:
    def __init__(self):
        self.papers = []
        self.seen_dois = set()
        self.seen_hashes = set()

    def generate_title_hash(self, title):
        """Creates a clean alphanumeric hash for deduplication."""
        if not title:
            return ""
        # Remove whitespace, punctuation, and lowercase
        clean_title = re.sub(r'[^a-z0-9]', '', title.lower())
        return hashlib.md5(clean_title.encode()).hexdigest()

    def add_paper(self, paper_dict):
        """Unified deduplication and addition logic."""
        doi = paper_dict.get('doi')
        title = paper_dict.get('title', '')
        title_hash = self.generate_title_hash(title)

        # 1. Deduplicate by DOI
        if doi and doi in self.seen_dois:
            return False
        
        # 2. Deduplicate by Title Hash
        if title_hash in self.seen_hashes:
            return False

        # If clean, add to tracking sets and list
        if doi:
            self.seen_dois.add(doi)
        self.seen_hashes.add(title_hash)
        
        paper_dict['clean_title_hash'] = title_hash
        self.papers.append(paper_dict)
        return True

    def fetch_ssrn_elsevier(self):
        """Fetch papers from SSRN via Elsevier API."""
        print(f"[*] Fetching SSRN via Elsevier API...")
        url = "https://api.elsevier.com/content/search/scidir" # ScienceDirect/SSRN search
        headers = {
            "X-ELS-APIKey": ELSEVIER_API_KEY,
            "Accept": "application/json"
        }
        params = {
            "query": "subj:ECON OR subj:FINA", # Economics or Finance
            "count": 10,
            "sort": "-coverDate",
            "view": "COMPLETE"
        }
        
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            results = data.get('search-results', {}).get('entry', [])
            added = 0
            for entry in results:
                # Map Elsevier schema to unified schema
                paper = {
                    "title": entry.get('dc:title'),
                    "authors": entry.get('dc:creator', 'Various Authors'),
                    "abstract": entry.get('description', 'No abstract available.'),
                    "published_date": entry.get('prism:coverDate'),
                    "source": "SSRN/Elsevier",
                    "pdf_url": entry.get('link', [{}])[0].get('@href'),
                    "doi": entry.get('prism:doi')
                }
                if self.add_paper(paper):
                    added += 1
            print(f"    - Added {added} papers from SSRN.")
        except Exception as e:
            print(f"    [!] Elsevier API Error: {e}")

    def fetch_rss_feed(self, url, source_name):
        """Generic RSS parser for institutional feeds."""
        print(f"[*] Fetching {source_name} RSS Feed...")
        try:
            feed = feedparser.parse(url)
            added = 0
            for entry in feed.entries:
                # Handle date parsing
                pub_date = entry.get('published')
                if pub_date:
                    try:
                        pub_date = date_parser.parse(pub_date).strftime("%Y-%m-%d")
                    except:
                        pub_date = datetime.now().strftime("%Y-%m-%d")

                paper = {
                    "title": entry.get('title'),
                    "authors": entry.get('author', source_name),
                    "abstract": entry.get('summary', 'No abstract available.'),
                    "published_date": pub_date,
                    "source": source_name,
                    "pdf_url": entry.get('link'),
                    "doi": entry.get('prism_doi', None)
                }
                if self.add_paper(paper):
                    added += 1
            print(f"    - Added {added} papers from {source_name}.")
        except Exception as e:
            print(f"    [!] RSS Error ({source_name}): {e}")

    def run_ingestion(self):
        # 1. SSRN (Elsevier API)
        self.fetch_ssrn_elsevier()
        
        # 2. BIS (RSS)
        self.fetch_rss_feed(BIS_RSS_URL, "BIS")
        
        # 3. FED (RSS)
        self.fetch_rss_feed(FED_RSS_URL, "Fed Staff Reports")

        print("\n" + "="*50)
        print(f"INGESTION COMPLETE: {len(self.papers)} Unique Papers Found")
        print("="*50 + "\n")

        for idx, p in enumerate(self.papers[:15]): # Show first 15 for demo
            print(f"[{idx+1}] {p['title']}")
            print(f"    Date: {p['published_date']} | Source: {p['source']}")
            print(f"    DOI: {p['doi'] or 'N/A'}")
            print("-" * 30)

if __name__ == "__main__":
    ingester = ResearchIngester()
    ingester.run_ingestion()
