# Déploiement frontend sur Vercel

## Import du projet

1. Connectez [Vercel](https://vercel.com) au dépôt GitHub `theLoggyStudio/Toa-Ai`.
2. **Root Directory** : `frontend` (obligatoire — le monorepo a le backend à la racine).
3. Framework : **Vite** (détecté via `vercel.json`).
4. Build : `npm run build` · Output : `dist`.

## Variables d'environnement (Vercel → Settings → Environment Variables)

| Variable | Valeur | Environnements |
|----------|--------|----------------|
| `VITE_API_URL` | `http://127.0.0.1:8000` | Production, Preview, Development |

Le backend Toa AI tourne **en local** sur la machine de l'utilisateur. Le frontend Vercel appelle donc toujours `127.0.0.1:8000` depuis le navigateur du client.

## Backend local (CORS)

Après le premier déploiement, notez l’URL Vercel (ex. `https://toa-ai.vercel.app`) et mettez à jour le fichier `backend/.env` :

```env
FRONTEND_ORIGIN=https://votre-projet.vercel.app
```

Redémarrez le backend (`npm start`). Sans cette étape, le navigateur bloquera les requêtes API (CORS).

Pour plusieurs origines (preview + prod), séparez par des virgules :

```env
FRONTEND_ORIGIN=http://localhost:5173,https://toa-ai.vercel.app
```

## Vérification

- Build local : `cd frontend && npm run build`
- Preview Vercel : ouvrir l’URL de déploiement, lancer le backend local, puis tester un upload.
