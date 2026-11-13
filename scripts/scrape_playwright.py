"""Scraping avec Playwright pour les sites qui bloquent requests (anti-bot, JS-rendered).

Nécessite l'installation de Playwright:
    pip install playwright
    playwright install chromium

Usage:
    from scripts.scrape_playwright import scrape_with_playwright
    html = scrape_with_playwright("https://www.banque-france.fr/", timeout=30)
"""

from __future__ import annotations

import sys
import os
from typing import Optional


def scrape_with_playwright(url: str, timeout: int = 30, wait_until: str = "networkidle") -> Optional[str]:
    """Récupère le HTML d'une page avec Playwright (navigateur headless Chromium).

    Utile pour les sites qui bloquent requests (403, anti-bot) ou qui rendent
    le contenu en JavaScript (React, Vue, etc.).

    Retourne le HTML de la page ou None en cas d'échec.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "[!] Playwright n'est pas installé. Installez-le avec:\n"
            "    pip install playwright\n"
            "    playwright install chromium",
            file=sys.stderr,
        )
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.goto(url, wait_until=wait_until, timeout=timeout * 1000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"[!] Playwright error for {url}: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/scrape_playwright.py <URL>")
        sys.exit(1)
    url = sys.argv[1]
    html = scrape_with_playwright(url)
    if html:
        print(f"[OK] {len(html)} octets récupérés depuis {url}")
        print(html[:500])
    else:
        print(f"[FAIL] Impossible de récupérer {url}")
        sys.exit(1)
