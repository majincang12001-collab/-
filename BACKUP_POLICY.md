# Backup and recovery policy

## Commit to Git
- Application source (`index.html`, `server.py`)
- Architecture notes and reproducible experiment scripts
- Dependency manifests, deployment instructions, test fixtures without personal data

## Never commit to a public repository
- `data/access.key`, cookies, API keys, passwords, Kaggle credentials
- User stories, private character sheets, uploaded files, generated media
- Model weights unless license and distribution rights have been checked

## Backup cadence
1. Commit source after each verified milestone.
2. Export Kaggle notebooks as `.ipynb` after each successful experiment.
3. Copy generated assets and project JSON to a separate private backup location.
4. Keep at least one copy outside the Box VM; Box storage is not the only backup.

## Restore
1. Clone the repository.
2. Install Python 3.10+.
3. Create `data/access.key` with a new random secret.
4. Run `python3 server.py` and verify `/api/health`.
5. Restore private project data only from the separately protected backup.