---
name: argos-acknowledgement
description: Maintain ARGOS initial acknowledgement phrase selection. Use when the user says ARGOSの最初の返事、初期待機応答、アクノレッジ、アクノレンジ、acknowledgement、やってみるねがおかしい、知ってる系の返事がおかしい、などを直したい場合。Always edit ~/argos-acknowledgement-api first, not ~/argos, unless the user explicitly asks to change ARGOS core.
---

# ARGOS Acknowledgement

## Workflow

1. Work in `~/argos-acknowledgement-api`.
2. Prefer editing `rules.yml` for phrase-selection changes.
3. Update `tests/test_main.py` for every new phrase rule.
4. Update `README.md` when user-visible behavior changes.
5. Run `uv run pytest --cov=. --cov-report=term-missing`.
6. Restart only the acknowledgement daemon after tests pass:

```bash
sudo systemctl restart argos-acknowledgement-api.service
```

7. Verify the live API with `POST /select`. Source the token from `~/argos-acknowledgement-api/.env` or `~/argos/.env`, but never print secrets.

## Do Not

- Do not change `~/argos/src/argos/services/acknowledgement` for phrase policy unless explicitly asked.
- Do not restart `argos.service` for acknowledgement API rule-only changes.
- Do not commit unless the user explicitly asks.

## Useful Files

- `~/argos-acknowledgement-api/rules.yml`: suffix-to-phrase rules.
- `~/argos-acknowledgement-api/main.py`: FastAPI endpoint and rule loader.
- `~/argos-acknowledgement-api/tests/test_main.py`: API behavior tests.
- `/etc/systemd/system/argos-acknowledgement-api.service`: live service definition.
