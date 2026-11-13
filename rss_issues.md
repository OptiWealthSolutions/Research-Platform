# Problèmes Connus - Flux RSS & Intégration de données

## État actuel
La plateforme fonctionne, mais certains flux et sources présentent des anomalies identifiées ci-dessous.

### 1. Erreur Scopus/Elsevier "Expecting value: line 1 column 1 (char 0)"
**Symptôme** : Dans `backend_log.txt` : `Scopus error: Expecting value: line 1 column 1 (char 0)`  
**Cause** : L'API Elsevier/Scopus par défaut utilise une clé invalide (`f6a884c51312665bd93f3a3ea91e1f8c`). Le service renvoie une page HTML (page de connexion/erreur) au lieu du JSON attendu, ce qui fait échouer `resp.json()`.  
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