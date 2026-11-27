# Changes 2026-11-26: automated larva counts on field photographs (opt-in)

| File | Change |
|---|---|
| `server/larva_detect.py` | New. `GET /larva-detection/status`; `POST /photos/{id}/detections` (supervisors); `GET /photos/{id}/detections`. Calls the Roboflow Serverless model `larvae_new-dataset-8000/1` with `inference-sdk`, key from `ROBOFLOW_API_KEY` in the Authorization header. Boxes below the confidence threshold (default 0.4) are dropped. A failed call is stored as failed with no count. |
| `server/db.py` | Migration 7: `photo_detections`. |
| `server/surveillance.py` | The dossier's photographs carry their latest successful automated count. |
| `web/js/views/dossier.js`, `web/css/app.css` | Each photograph shows "Automated count, unverified". Supervisors see a "Count larvae" button only when the installation has switched counts on; otherwise the reason is shown. Service-worker cache v7. |
| `requirements-detection.txt` | New, optional: `inference-sdk>=1.5.0`. Not in `requirements.txt`. |
| `tests/test_larva_detect.py` | Four tests: off by default, key required, count stored as an unverified reading without changing the observation, service failure records no count. 78 tests pass. |

## Rules

- **Off unless the operator sets `RINDA_LARVA_DETECTION=roboflow`**, because photographs are sent to a third party.
- **A count is a reading.** It does not confirm larvae, species or stage, and it changes no observation, status or
  decision.
- **Not on the hosted site.** The SDK brings OpenCV, which is too heavy for the 512 MB hosted plan.
- **The model is uninspected.** Its classes, training data and accuracy are unknown (see
  `08_Code/larva-detection/README.md`).
