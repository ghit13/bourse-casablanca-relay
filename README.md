# Bourse Casablanca Relay — version stable/auditable

Cette version n'essaie plus de parser les pages HTML ou les PDF de la Bourse à la main.

## Source marché
Le script utilise `casabourse==0.1.5`, une bibliothèque Python dédiée à la Bourse de Casablanca.
Elle interroge les données publiques de `casablanca-bourse.com`.

## Source fondamentaux / publications
AMMC :
- communiqués émetteurs
- liste des états financiers

## Fichiers produits
- `data/latest.json` : données validées utilisées par Excel
- `data/latest_candidate.json` : extraction du jour, même si elle échoue au contrôle
- `data/quality_status.json` : résultat des contrôles automatiques
- `data/raw_live_market.csv` : données brutes marché pour audit
- `data/raw_live_market_columns.json` : colonnes réellement reçues
- `data/raw_instruments.csv` : univers brut
- `data/market.csv` : données marché normalisées
- `data/universe.csv` : univers dynamique
- `data/audit_sample.csv` : 10 valeurs faciles à vérifier manuellement
- `data/ammc_news.csv` : publications AMMC
- `data/history.csv` : historique construit jour après jour

## Garde-fou
`latest.json` n'est pas remplacé si les contrôles sont BLOQUÉS.
Les fichiers bruts et diagnostics sont quand même commités pour qu'on puisse voir exactement ce qui a été reçu.

## Vérification manuelle
Après un run vert :
1. ouvrir `data/audit_sample.csv`
2. contrôler 5 à 10 cours sur la Bourse de Casablanca
3. reporter les mêmes valeurs dans la feuille `CONTROLE_QUALITE` de l'Excel
4. ne considérer les signaux qu'une fois le contrôle en `OK`
