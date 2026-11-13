"""Diagnostic des flux RSS : teste chaque URL et indique laquelle est inaccessible.

Run:  /opt/anaconda3/bin/python3 scripts/diagnose_feeds.py [--timeout 15]
"""
import sys
import os
import time
import argparse
import requests
import feedparser

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend import sources as catalog


USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def get_headers():
    import random
    return {"User-Agent": random.choice(USER_AGENTS), "Accept": "application/rss+xml, application/xml, text/xml, */*"}


def diagnose_feed(source_name: str, url: str, timeout: int = 15) -> dict:
    """Teste un flux RSS et retourne un dict avec le statut et le détail."""
    result = {
        "source": source_name,
        "url": url,
        "status": "unknown",
        "status_code": None,
        "entries": 0,
        "error": None,
        "elapsed_ms": 0,
    }
    start = time.time()
    try:
        resp = requests.get(url, headers=get_headers(), timeout=timeout)
        result["status_code"] = resp.status_code
        result["elapsed_ms"] = int((time.time() - start) * 1000)

        if resp.status_code == 200:
            try:
                feed = feedparser.parse(resp.content)
                bozo = getattr(feed, "bozo", 0)
                entries = len(feed.entries) if feed.entries else 0
                result["entries"] = entries
                if bozo and entries == 0:
                    result["status"] = "parsing_error"
                    result["error"] = f"feedparser bozo=1, 0 entries"
                elif entries == 0:
                    result["status"] = "empty"
                    result["error"] = "flux vide (0 entrées)"
                else:
                    result["status"] = "ok"
            except Exception as e:
                result["status"] = "parsing_error"
                result["error"] = f"feedparser error: {type(e).__name__}: {str(e)[:100]}"
        elif resp.status_code in (301, 302, 303, 307, 308):
            result["status"] = "redirect"
            result["error"] = f"redirection vers {resp.headers.get('Location', '?')}"
        elif resp.status_code == 403:
            result["status"] = "forbidden"
            result["error"] = "403 Forbidden (blocage anti-bot)"
        elif resp.status_code == 404:
            result["status"] = "not_found"
            result["error"] = "404 Not Found (URL invalide)"
        elif resp.status_code == 429:
            result["status"] = "rate_limited"
            result["error"] = "429 Too Many Requests"
        elif resp.status_code >= 500:
            result["status"] = "server_error"
            result["error"] = f"{resp.status_code} Server Error"
        else:
            result["status"] = "http_error"
            result["error"] = f"HTTP {resp.status_code}"

    except requests.exceptions.Timeout:
        result["status"] = "timeout"
        result["error"] = f"timeout après {timeout}s"
        result["elapsed_ms"] = int((time.time() - start) * 1000)
    except requests.exceptions.ConnectionError as e:
        result["status"] = "connection_error"
        result["error"] = f"connexion échouée: {str(e)[:100]}"
        result["elapsed_ms"] = int((time.time() - start) * 1000)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {str(e)[:100]}"
        result["elapsed_ms"] = int((time.time() - start) * 1000)

    return result


def run(timeout: int = 15):
    rss_sources = catalog.rss_sources()
    print(f"[*] Test de {len(rss_sources)} flux RSS (timeout={timeout}s)\n")

    results = []
    for source_name, url in rss_sources.items():
        r = diagnose_feed(source_name, url, timeout=timeout)
        results.append(r)

        # Affiche en temps réel
        status_icon = {
            "ok": "✅",
            "empty": "⚠️ ",
            "parsing_error": "⚠️ ",
            "redirect": "🔄",
            "forbidden": "🚫",
            "not_found": "❌",
            "rate_limited": "⏱️ ",
            "server_error": "💥",
            "http_error": "❌",
            "timeout": "⏱️ ",
            "connection_error": "🔌",
            "error": "❌",
        }.get(r["status"], "❓")
        err_txt = f" — {r['error']}" if r["error"] else ""
        print(f"  {status_icon} {source_name[:45]:45} [{r['status']:15}] {r['entries']:3} entrées {err_txt}")

    # Résumé
    print("\n" + "=" * 80)
    print("RÉSUMÉ")
    print("=" * 80)

    from collections import Counter
    status_counts = Counter(r["status"] for r in results)
    for status, count in status_counts.most_common():
        label = {
            "ok": "OK",
            "empty": "Vide",
            "parsing_error": "Erreur parsing",
            "redirect": "Redirection",
            "forbidden": "Interdit (403)",
            "not_found": "Introuvable (404)",
            "rate_limited": "Trop de requêtes (429)",
            "server_error": "Erreur serveur (5xx)",
            "http_error": "Erreur HTTP",
            "timeout": "Timeout",
            "connection_error": "Erreur connexion",
            "error": "Erreur",
        }.get(status, status)
        print(f"  {label}: {count}")

    print(f"\n  Total testé: {len(results)}")
    ok_count = status_counts.get("ok", 0)
    print(f"  Opérationnels: {ok_count}/{len(results)} ({ok_count/len(results)*100:.0f}%)")

    # Liste des sources en échec
    failed = [r for r in results if r["status"] != "ok"]
    if failed:
        print(f"\n  Sources en échec ({len(failed)}):")
        for r in failed:
            print(f"    - {r['source']} ({r['kind'] if 'kind' in r else 'N/A'}): {r['error'] or r['status']}")

    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=15, help="timeout en secondes par requête")
    args = ap.parse_args()
    run(timeout=args.timeout)
