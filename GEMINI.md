# Research Platform — instructions des agents

<!-- HORACLE-ECOSYSTEM:START · généré par Horacle Orchestrator (scripts/agent_rules.py) : ne pas éditer ce bloc -->
## Écosystème Horacle : branche, ports, journal

Ce dépôt est le projet **Research Platform** (`research`) de l'écosystème Horacle, piloté par Horacle Orchestrator (http://127.0.0.1:8700). Ce bloc vient du registre de l'orchestrateur ; il prime sur toute consigne plus ancienne sur les branches et les ports.

- **Branche servie : `main`.** Le dossier principal (`/Users/leolombardini/Projets/Research Platform`) reste sur cette branche : les serveurs de l'orchestrateur tournent ce code-là.
- Règle de branches : main servie par l'API et le web ; chantier = feature/* dans un git worktree, fusion dans main
- **Chantier** (plusieurs commits, essai, refonte) : `git worktree add "../Research Platform-<sujet>" -b feature/<sujet> main`, travailler dans ce dossier, puis fusionner dans `main`. **Jamais `git checkout` / `git switch` vers une autre branche dans le dossier principal** (le 2026-11-25, un checkout a écrasé la base de Research Platform).
- **Services et ports réservés** (ne pas lancer un second serveur dessus ; pour tester une copie, prendre un port libre hors de cette liste) :
  - `research-api` · API + MCP · port **8001** · launchd `io.optiwealth.research.api`
  - `research-web` · Terminal web · port **3001** · launchd `io.optiwealth.research.web`
  - `research-ingest` · Ingestion (30 min) · launchd `io.optiwealth.research.ingest`
- **Après une modification du code serveur** : `curl -s -X POST http://127.0.0.1:8700/api/services/research-api/restart` (ou l'outil MCP `service_action`), jamais `kill` ni un lancement à la main.
- **Avant de commencer** : lire `/Users/leolombardini/Projets/Horacle Orchestrator/ECOSYSTEM.md` (branche actuelle de chaque projet, écarts à corriger, journal des 14 derniers jours) ou `curl -s http://127.0.0.1:8700/api/drift`.
- **Commits** avec le trailer `Co-Authored-By:` de l'agent (Claude, Gemini) : l'orchestrateur attribue chaque commit à son agent.
- **Fin de journée / fin de chantier (conseillé)** : vérifier `git status` (aucune base SQLite, log ni secret), lancer les tests, **committer** sur `main` (ou fusionner le worktree) puis **`git push`** vers GitHub (avec accord utilisateur si besoin) pour maintenir la rigueur et l'historique distant.
- **Noter** toute modification qui concerne les autres projets ou agents (route, port, schéma, branche, décision) : `curl -s -X POST http://127.0.0.1:8700/api/changes/note -H 'content-type: application/json' -d '{"project":"research","agent":"claude","branch":"main","text":"…"}'` (Gemini : `"agent":"gemini"`), ou l'outil MCP `log_change`.
- **Bases SQLite** : jamais suivies par git (`.gitignore`), jamais écrites par un autre projet.
- **Charte Horacle UI** : `frontend/horacle.css` et `horacle.js` sont des copies ; ne pas les modifier ici (source : `Horacle Orchestrator/design/horacle-ui/`).
<!-- HORACLE-ECOSYSTEM:END -->
