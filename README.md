# Bourse Casablanca Relay V7

Ce dépôt récupère uniquement des sources officielles publiques :
- Bourse de Casablanca : univers, cotations, indices, avis.
- AMMC : communiqués et états financiers publiés.

Le workflow GitHub Actions écrit les fichiers dans `data/`.

## Première exécution
1. Onglet Actions.
2. Sélectionner **Update official data**.
3. **Run workflow**.
4. Attendre le ✓ vert.
5. Vérifier que `data/latest.json` contient une date `generated_at_utc`.

Le fichier local Mac lit ensuite :
`https://raw.githubusercontent.com/UTILISATEUR/DEPOT/main/data/latest.json`
