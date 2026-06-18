#!/bin/bash

# Arrêt en cas d'erreur
set -e

# Ajout de tous les fichiers
git add .

# Commit avec message par défaut
git commit -m "maj"

# Push vers la branche courante
git push