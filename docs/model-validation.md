# Model validation

Status: **research stage, not validated for intervention decisions.** This document states what the model predicts,
how it was tested, what the results are, and why it must not yet guide treatment.

## 1. Prediction target

Target A, *new surface-water formation*: a monitoring point is dry on the issue date *t* and for the 3 preceding days,
and wet at *t + h* (h = 7 or 14 days). The outcome is matched to the nearest Sentinel-1 pass within ±2 days of *t + h*;
rows with no pass in that window are dropped, not imputed.

Wet is defined by prototype thresholds on descending-pass backscatter within a 50 m buffer: VV ≤ −17 dB **and**
VH ≤ −23 dB. These thresholds are **not calibrated** against a field or optical reference.

The target is **not** larval presence, *Anopheles* presence, productive habitat or transmission risk. Target B
(potentially suitable habitat) needs Sentinel-2 and terrain; Target C (confirmed *Anopheles* habitat) needs verified
larval surveys. Both are blocked by missing data.

Relevant vectors in the deployment context are *An. gambiae* s.l., *An. arabiensis* and *An. funestus* (to be
confirmed with RBC for Gikomero). The model does not distinguish species.

## 2. Data and provenance

| Source | Coverage | Notes |
|---|---|---|
| Sentinel-1 GRD, descending | 600 sites, 2023-03-01 to 2026-01-20 | Copernicus via Earth Engine; 50 m buffer primary, 10–250 m as context features |
| Gikomero automatic weather station | 979 days, 2023-03-22 to 2026-01-15, 95% day coverage | One station: no spatial weather variation. Humidity used only on complete days |
| Sentinel-2, terrain, NWP, larval surveys | absent | Gated off in configuration |

111 features: rainfall windows, lags, intensity and anomalies; temperature and degree-days; gated humidity; radar
state, changes, multi-scale backscatter and a per-site rain-response slope; acquisition quality. Four leakage checks
(no future columns, no perfect predictor, no future-named columns, prefix stability) run on every table and passed.

## 3. Method

- XGBoost (800 trees, depth 5, learning rate 0.03, class-weighted), median imputation inside the pipeline.
- Temporal holdout: train to 2024-12-31; calibrate (isotonic) and choose the threshold on 2025-01-01 to 2025-08-31;
  test once on 2025-09-01 onward. The test window was not used for any choice.
- Model selection on blocked cross-validation (spatial blocks, temporal blocks, forward chaining); a random split is
  reported only to show how optimistic it would have been.
- Uncertainty: Mondrian conformal prediction (α = 0.1) gives a prediction set per point; ensemble spread gives an
  uncertainty band.

## 4. Results on the untouched test window

| Metric | 7-day | 14-day | Why it matters |
|---|---|---|---|
| Base rate | 0.037 | 0.023 | Formation is rare; accuracy would be ~96% by predicting nothing, so it is not reported |
| PR-AUC (95% block-bootstrap CI) | 0.581 (0.426–0.678) | 0.310 | Ranking quality for a rare positive class; compare with the base rate |
| ROC-AUC | 0.961 | 0.890 | Reported for completeness; flattered by the many easy negatives |
| Precision / recall at operating threshold | 0.65 / 0.53 | — | Threshold chosen on the calibration window |
| MCC | 0.570 | — | Balanced single-number summary for imbalanced classes |
| Brier score / ECE | 0.022 / 0.004 | — | Probability calibration |
| Conformal coverage (target 90%) | 90.8% | — | Prediction sets meet their guarantee |

### Comparison with simple references (same test rows)

| Reference | PR-AUC 7-day | PR-AUC 14-day |
|---|---|---|
| Model | 0.581 | 0.310 |
| Climatology (base rate) | 0.037 | 0.039 |
| Persistence: 30-day water frequency | **0.628** | **0.629** |
| Persistence: wet in last 7 days | 0.339 | 0.384 |
| Persistence: formed in last 30 days | 0.553 | 0.556 |

**A persistence rule outperforms the model at both horizons.** This is the main reason the model is labelled research stage.

### Operational validation (as shown in Replay & Validation)

Using the verification categories (High = conformal set {formation}), over all 45 test issues:

| | 7-day | 14-day |
|---|---|---|
| Site-issues evaluated | 24,339 | 22,734 |
| Formations observed | 912 | 887 |
| High-priority calls | 2,959 | 2,314 |
| Precision of calls | 26.4% | 22.4% |
| Detection rate | 85.6% | 58.4% |
| Called or flagged "insufficient evidence" | 93.9% | 93.6% |
| Missed while "lower priority" | 56 | 57 |

Interpretation: as an inspection prioritiser the 7-day output finds most new water but three of four calls are false
alarms. That is acceptable for sending an inspector, and unacceptable for sending larvicide, which is why the platform
never lets a forecast authorise treatment.

## 5. Limitations

1. Outcome is radar water with uncalibrated thresholds, not larvae; no field validation.
2. Does not beat a persistence baseline.
3. Hindcast: no rainfall forecast, so rain after the issue date is unknown (explains the 14-day drop).
4. One weather station and one sector: no evidence of transfer to other districts or agro-ecological zones.
5. Test window is September 2025 to January 2026 (short rains and transition); long-rains performance is unknown.
6. The latest issue lacks five days of weather inputs; the model imputes them with training medians.
7. Live inputs are not connected; the archive ends 2026-01-20.

## 6. Verification categories (operational rules)

Defined in `config/operational_rules.json`, version `2026-10-12.1`, **approval by the operational partner pending**.

| Category | Rule | Meaning |
|---|---|---|
| High priority for verification | conformal set = {formation} | Inspect before the target date |
| Moderate priority | set = {no formation} and p ≥ agreed threshold | **Disabled**: no threshold agreed |
| Insufficient evidence to classify | set = {formation, no formation} | Only an inspection can classify |
| Lower current priority | set = {no formation} | Not evidence of absence of larvae |

No category uses an arbitrary probability cut-off. The 0.2-wide "risk classes" in the study configuration are retained
in the data for traceability but are no longer shown as operational categories.

## 7. Replay and leakage

Replay serves the products of the published hindcast: one model fitted to 2024-12-31 and calibrated to 2025-08-31,
applied to issues from 2025-09-03. Every feature is dated on or before its issue date (leakage guard passed). Each
replayed forecast shows the last radar pass and weather day it used. No forecast is regenerated with later information.

To replay *operational* forecasts in future, each issued forecast must be stored with its inputs' timestamps and the
model fingerprint at the time of issue. The current product files already carry issue date and model fingerprint; a
forecast store with immutable records is needed once live ingestion starts (backlog item B-03).

## 8. What would move the model out of research stage

1. Beat the persistence baseline on a held-out period (for example by adding the persistence rule as a feature).
2. Calibrate water thresholds against field or Sentinel-2 reference.
3. Validate against verified larval observations collected through Field Surveillance (Target C).
4. Add an NWP rainfall forecast and re-evaluate the 14-day horizon.
5. Test on a second sector with its own weather station.
