# social-jurimetrie-local

Application Streamlit d'aide à la recherche jurisprudentielle en droit du travail et droit de la sécurité sociale.

Devise fonctionnelle : **Extraction automatique, validation humaine, citation sécurisée.**

## Fonctionnalités V1

- Import TXT ou copier-coller d'une décision.
- Stockage SQLite local au runtime.
- Extraction prudente de date, juridiction, numéro de pourvoi et numéro RG.
- Classification indicative par mots-clés.
- Recherche plein texte.
- Fiche décision avec validation avocat.
- Matrice exportable en CSV.
- Avertissement méthodologique intégré.

## Déploiement Streamlit Cloud

Dans Streamlit Community Cloud :

- Repository : `raphaelohayon89-cpu/social-jurimetrie-local`
- Branch : `main`
- Main file path : `app.py`

Aucun secret n'est requis pour cette V1.

## Limite importante

Les extractions sont heuristiques. L'application ne doit jamais être utilisée pour citer une décision sans vérification humaine de la source.
