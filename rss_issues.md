# Problèmes Connus - Flux RSS & Intégration de données

## État actuel
La plateforme fonctionne, mais certains flux et sources présentent des anomalies identifiées ci-dessous.

### 1. Erreur Scopus/Elsevier "Expecting value: line 1 column 1 (char 0)"
**Symptôme** : Dans `backend_log.txt` : `Scopus error: Expecting value: line 1 column 1 (char 0)`  
**Cause** : L'API Elsevier/Scopus par défaut utilisait une clé codée en dur (retirée le 2026-11-13 ; considérer l'ancienne clé comme publique). Le service renvoie une page HTML (page de connexion/erreur) au lieu du JSON attendu, ce qui fait échouer `resp.json()`.  
**Localisation** : `scripts/ingest.py` - fonction `ingest_scopus()`  
**Fix appliqué** : Ajout d'un gestionnaire `try/except json.JSONDecodeError` qui affiche un message explicite et retourne 0.  
**Résolution requiert** : Obtenir une clé API Elsevier gratuite sur https://api.elsevier.com/ et la définir :
```bash
export ELSEVIER_API_KEY=votre_cle_ici
```

### 2. 27 sources "portal" en statut "error" avec 0 document
**Symptôme** : Dans `/api/health` ou `/mcp/platform_health` : sources de type `portal` avec `total_docs: 0`, `last_published: null`, `status: error`  
**Cause** : Ce sont des desks de recherche **login-walled / JS-rendered** (ex: SEB, MUFG, Westpac, Nordea, BlackRock, PIMCO, Vanguard, etc.) qui ne peuvent pas être auto-ingérés. Ils sont toutefois **enregistrés** dans le catalogue afin que l'interface puisse afficher des liens directs vers leurs portails.  
**Localisation** : `backend/sources.py` - catalogue `CATALOG` (section "PORTAL")  
**Statut** : **Comportement attendu** - Ces sources servent de repères de navigation, pas de contenu extrait. L'affichage d'erreur dans le UI peut être atténué côté frontend (voir ci-dessous).

### 3. Anciennement : collision de hash de titre (UNIQUE constraint)
**Symptôme** : `UNIQUE constraint failed: papers.clean_title_hash` dans les logs  
**Cause** : La fonction `normalize_title()` ne gardait que `[a-z0-9]`, ce qui faisaitCollider différents titres (ex: "EUR/USD: Fed press" et "EUR USD Fed Press"→deux titres différents mais hash identique).  
**Localisation** : `scripts/ingest.py` - fonction `normalize_title()` et utilisation de `clean_title_hash`  
**Fix appliqué** : Remplacement par `title_hash()` utilisant `hashlib.sha256()` avec prise en compte de la source :
```python
def title_hash(title: str, source: str = "") -> str:
    combined = f"{source}|{title or ''}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]
```
**Résultat** : Plus de collisions de hash, insertion réussie des papers en doublon.

### 4. Sources "scrape" en échec (Banque de France, Schroders, Danske Bank)
**Symptôme** : Dans les logs : `[-] Banque de France (scrape)... [!] requests a échoué (HTTP ?), tentative avec Playwright...`  
**Cause** : Rate-limiting du site cible, certificats SSL, ou échec du fallback Playwright (manque de `playwright install chromium`).  
**Localisation** : `scripts/ingest.py` - fonctions de scrape, et `backend/sources.py` - configurations de scrape avec `render: True`  
**Solution** :
- S'assurer que Playwright est installé : `playwright install chromium`
- Vérifier les config `scrape` dans `backend/sources.py` (paramètres `wait`, `render`, `exclude_re`, etc.)
- Certains sites (Bundesbank, Amundi) sont connus pour fonctionner en mode scrape, d'autres (Banque de France) nécessitent Playwright.

### 5. Flux RSS "304 Not Modified" (comportement attendu)
**Symptôme** : Dans les logs : `[-] Fed Press: 304 Not Modified`, `[-] WSJ Markets: 304 Not Modified`, etc.  
**Cause** : Le serveur RSS renvoie "304 Not Modified" quand aucun nouveau article n'est depuis la dernière vérification. C'est un comportement HTTP standard, pas une erreur.  
**Statut** : **Attendu** - Pas d'article nouveau = pas d'insertion dans la BDD, mais pas d'erreur non plus. Le frontend affiche correctement "0 new".

---

## Résumé des actions

| Statut | Action |
|--------|--------|
| ✅ Fixé | Collision de hash de titre (UNIQUE constraint) |
| ✅ Fixé | Gestion d'erreur JSON Scopus/Elsevier |
| ⚠️ Restant | Clé API Elsevier invalide → définir `ELSEVIER_API_KEY` |
| ⚠️ Restant | Sources portal (27 sources) → comportement attendu, gestion UI |
| ⚠️ Restant | Scrapers avec Playwright → installer `playwright install chromium` |

## Utilisation corrigée

Pour démarrer la plateforme avec une clé API valide :
```bash
export ELSEVIER_API_KEY=votre_cle_ici
./start.sh
```

Les logs afficheront alors davantage d'articles ingestés et les erreurs "Expecting value" disparaîtront pour les sources disposant d'une clé valide.

---

## Revue du 2026-11-13 (intégration Models-Macro)

Corrigé :
- **Exposition réseau** : API et UI liées à 127.0.0.1 ; CORS limité aux origines locales, sans
  credentials ; requêtes d'écriture d'une autre origine refusées (403).
- **SSRF** des proxys PDF : schémas http/https seuls, hôtes privés / loopback / link-local
  refusés, chaque redirection revérifiée (`backend/netguard.py`).
- **Clé Elsevier codée en dur** retirée : `ELSEVIER_API_KEY` en variable d'environnement,
  Scopus sauté sinon.
- **Dates fabriquées** : l'heure d'ingestion était collée sur les dates des flux, heure de
  Paris mêlée à l'UTC (148 lignes datées dans le futur). Désormais UTC partout,
  `published_precision` (`datetime` / `date` / `seen` / `suspect`) et `available_at`.
  Les éléments scrapés sans date ne prennent plus la date du jour.
- **Recherche booléenne** : parenthèses jamais tokenisées, AND implicite ignoré ; nouveau
  parseur `backend/search.py`. Filtres de catégories intersectés (toute requête
  multi-catégories vide) ; paires `EURUSD` vs `EUR/USD` ; `min_importance` sans effet ;
  `papers_by_asset` en une seule chaîne LIKE ; `search_by_keywords` importait une fonction
  absente.
- **Deux planificateurs** concurrents sur SQLite sans verrou : un seul (launchd), verrou
  fichier, WAL + `busy_timeout`. Chemin de base relatif rendu absolu (`RP_DB_PATH`).
- **Faux « 0 article »** quand un flux ne renvoyait que des articles déjà stockés ; desks
  « portal » comptés en erreur ; « HTTP ? » systématique (test de vérité sur `Response`).
- **Calendrier codé en dur** (périmé après le 2026-12-18) remplacé par le flux ForexFactory
  hebdomadaire, copie disque par fichier (le flux renvoie 429 si on l'interroge trop).
- **Texte intégral** téléchargé pour FinBERT puis jeté : désormais conservé (`full_text`).
- **Biais des « FX calls »** : une devise seule donnait `JPY/USD`, `CNY/USD` (convention
  inversée) ; désormais `USD/JPY`… avec le signe inversé, et `bias_inferred: true` (mots clés +
  FinBERT, pas la recommandation du desk).
- **Repli Playwright** du scraper : importait `scripts.scrape_playwright`, module absent ; le
  repli échouait toujours. Remplacé par curl_cffi (empreinte Chrome) puis
  `backend.scrapers._render_html`.
- Zones AUD / NZD et banques centrales BoC, RBA, RBNZ, BNS ajoutées ; 140 sources dont 88 RSS.

Connu, non corrigé :
- **WSJ Markets / WSJ World** : flux figés (dernier article janvier 2025) ; WSJ a fermé ses RSS
  publics.
- **IMF, OECD, Rabobank, ForexFactory news** : bloqués ou 404, non ajoutés.
- **Statut « error » = périmé > 30 jours** : vrai aussi pour des sources peu fréquentes
  (Bank Overground, discours BNS) ; lire `days_since_last` avant de conclure.
- **ForexFactory** : `ff_calendar_nextweek.json` n'apparaît qu'en fin de semaine ; un weekly
  rédigé le samedi peut ne pas avoir l'agenda S+1 : prendre la source T1 ou l'agenda du Hub.
