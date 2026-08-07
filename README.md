# Toa AI

Traducteur automatique de mangas et manhwas — monorepo React (Vercel) + FastAPI (local).

## Démarrage local

```bash
npm run install:all
npm start
```

- Frontend : http://localhost:3100/TOA.ai (traduction) · http://localhost:3100/eclat (Éclat)
- Backend : http://127.0.0.1:9400

Routes :
- `/TOA.ai` — traduction manga / manhwa (PayDunya)
- `/eclat` — **Éclat**, restauration photo (250–1000 FCFA selon les mégapixels)

Copiez `backend/.env.example` vers `backend/.env` et configurez vos clés.

## Déploiement

- **Frontend (Vercel)** : voir [frontend/DEPLOY_VERCEL.md](frontend/DEPLOY_VERCEL.md)
- **Backend** : hébergé en local (`http://127.0.0.1:9400`)

## Structure

```
frontend/   React 19 + Vite + Bootstrap 5
backend/    FastAPI + pipeline OCR / traduction / PDF
```
