# Déploiement backend sur Render (gratuit)

## URL attendue
Après le premier deploy : `https://toa-ai-api.onrender.com`  
(à confirmer dans le dashboard Render)

## Créer le service
1. Compte sur [render.com](https://dashboard.render.com)
2. **New** → **Blueprint** → repo `theLoggyStudio/Toa-Ai`
3. Valider le fichier [`render.yaml`](../render.yaml) à la racine
4. Renseigner les secrets (sync: false) dans le dashboard

## Secrets obligatoires
| Variable | Exemple |
|----------|---------|
| `BACKEND_PUBLIC_URL` | `https://toa-ai-api.onrender.com` |
| `CURSOR_API_KEY` | clé Cursor |
| `PAYDUNYA_MASTER_KEY` | … |
| `PAYDUNYA_TEST_*` / `PAYDUNYA_PROD_*` | clés PayDunya |

`FRONTEND_ORIGIN` est déjà `https://toa-ai.vercel.app` dans le Blueprint.

## Frontend Vercel
Variable d’environnement Production :
```
VITE_API_URL=https://toa-ai-api.onrender.com
```
Puis redeploy le projet frontend.

## Limites free
- Sleep après ~15 min d’inactivité (cold start ~1 min)
- Pas de disque persistant (uploads/PDF perdus au restart)
- 512 Mo RAM — jobs lourds peuvent échouer
