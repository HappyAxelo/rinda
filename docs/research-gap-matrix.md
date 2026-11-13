# Research-gap matrix and objective map

Prepared 2026-11-12. Generated from `config/research_alignment.json`, which the console also shows under Administration & Evidence, Research alignment.

Sources: the PhD proposal (Final5), its reference list, the systematic scoping review of 140 studies and the 5 November 2026 audit. Every study cited is in the proposal's reference list. Statements marked *project finding* are this project's own results and are not peer reviewed.

**Not done:** No new literature search was run for this matrix. The literature library (529 records) has not been searched in Scopus, Web of Science, IEEE Xplore or ACM directly, so the gap statements for the embedded-systems questions rest on targeted journal searches.

## Objectives and modules

| Objective | Paper and questions | Modules | Delivered | Still open |
|---|---|---|---|---|
| **Objective 1.** Forecast new surface water 7 to 14 days ahead against a recent-water baseline, and predict which forecast water becomes Anopheles larval habitat and how long remains before pupation. | Paper 1: habitat forecast (RQ1, RQ5) | Surveillance map, Historical replay, Model assurance, Data and models, Field operations | Forecast display, replay, baselines, dataset registry, reproducible training, model registry, field verification workflow. | Label reconstruction after the audit; prospective archive for H1; RBC entomological records for the habitat model (H5). |
| **Objective 2.** Carry the forecast to the field: test transfer to a second area, check satellite water labels against in-situ sensors, and link forecasting, sensing, drone or ground allocation and mission planning. | Paper 2: from forecast to flight (RQ3, RQ4) | Surveillance map, Field network, Drone operations, Data and models | Telemetry endpoint and node registry, sensor layer, connectors that wait for approved access, mission planning with approval, suitability assessment that assigns drone or ground treatment. | Sensor nodes in the field; a second area; an agreed rule for assigning habitats to drone or ground teams. |
| **Objective 3.** Choose the onboard water model by measured end-to-end latency, and develop and validate a probabilistic timing-and-placement controller for single-pass spraying. | Paper 3: edge AI and deadline-aware spraying (RQ2, RQ6) | Flight performance, Drone operations, Data and models | Flight-log ingestion and analytics (latency distribution, deadline-miss ratio, verification precision and recall), mission records, server-side efficiency measurements. | The onboard model, the controller, Jetson and flight measurements. These are experiments outside the console; RINDA records and analyses their logs. |

## Gap matrix

### G1. Forecasting new surface water, not only detecting existing water

| | |
|---|---|
| What existing studies have done | Satellite radar maps open and vegetated water that exists now (Hardy et al. 2019; Vanderhoof et al. 2023; Tran et al. 2022). |
| Their limitations | These studies classify the present. A map made on the day of a flight is out of date for water that forms afterwards. In the scoping review, 3 of 140 studies made a prospective forecast. |
| The unresolved gap | **No study forecasts, site by site, which dry locations will hold new water days ahead and tests that forecast against a simple recent-water rule on forecasts archived before the outcome.** |
| How RINDA addresses it | Issues and archives 7- and 14-day forecasts for 600 monitoring locations, replays any past issue using only data available then, and shows each forecast beside the recent-water baseline. |
| Research objective | Objective 1 · RQ1 · H1 |
| How the contribution will be evaluated | Difference in 7-day PR-AUC between the model and the recent-water rule B1 on publicly archived forecasts, lower 95% confidence limit above zero. |
| Where it stands | Preliminary, and narrowed by the 13 November 2026 correction. With Sentinel-1C and 1D corrected, PR-AUC is 0.599 (7 d) and 0.569 (14 d) against 0.534 and 0.484 for B1 (published before correction: 0.786 and 0.663). 95% of events are at the lake shore; inland there is no demonstrated skill. H1 needs forecasts archived before outcomes. |
| Sources | [Hardy et al. 2019, Remote Sensing 11(5):593](https://doi.org/10.3390/rs11050593); [Vanderhoof et al. 2023, Remote Sensing of Environment 288:113498](https://doi.org/10.1016/j.rse.2023.113498); [Tran, Menenti and Jia 2022, Remote Sensing 14(22):5721](https://doi.org/10.3390/rs14225721) |

### G2. Linking forecast water to Anopheles habitat suitability

| | |
|---|---|
| What existing studies have done | Habitat characteristics explain larval presence at surveyed water bodies: a model predicted An. funestus-positive habitats with 83% accuracy in Tanzania (Kahamba et al. 2024); model ensembles improved habitat maps in western Kenya (Zhou et al. 2024); a hydrological model predicted ponding in Ethiopia (Jiang et al. 2021). |
| Their limitations | These describe habitats that already exist and were surveyed. Only a minority of water bodies hold larvae: 11.2 to 16.5% in rural Tanzania (Msugupakulya et al. 2024). In the review, 31 studies predicted larval habitat and none predicted productive habitat. |
| The unresolved gap | **Forecast water has not been filtered to likely Anopheles habitats using routine programme records.** |
| How RINDA addresses it | Keeps water (target A) and confirmed breeding (target D) as separate models. Accepts RBC survey files as versioned datasets and records dipping results in the field workflow. No habitat model exists yet. |
| Research objective | Objective 1 · RQ5 · H5 |
| How the contribution will be evaluated | PR-AUC for Anopheles larval presence against a habitat-type-only rule, on survey visits held out by site block and season, then on random dipping samples. |
| Where it stands | Awaiting data. RBC historical records were requested on 14 October 2026. |
| Sources | [Kahamba et al. 2024, Parasites & Vectors 17:38](https://doi.org/10.1186/s13071-024-06119-6); [Zhou et al. 2024, Am J Trop Med Hyg 110(3):421-430](https://doi.org/10.4269/ajtmh.23-0108); [Jiang et al. 2021, Scientific Reports 11:10150](https://doi.org/10.1038/s41598-021-89576-8); [Msugupakulya et al. 2024, Malaria Journal 23:344](https://doi.org/10.1186/s12936-024-05172-x) |

### G3. Combining Sentinel-1, Sentinel-2, weather and terrain

| | |
|---|---|
| What existing studies have done | Sentinel-1 and Sentinel-2 have been compared for water mapping over long time series (Vanderhoof et al. 2023). |
| Their limitations | Fusion is usually assumed to help. It is rarely tested by removing one source at a time on a forecasting task. |
| The unresolved gap | **Whether optical, weather and terrain data add forecasting skill beyond radar history at site level.** |
| How RINDA addresses it | Model Assurance reports ablation by source. The data inventory lists Sentinel-1, Sentinel-2, Landsat, terrain and surface-water holdings with dates, resolution and quality. |
| Research objective | Objective 1 · RQ1 |
| How the contribution will be evaluated | Change in PR-AUC when a source is removed, with block-bootstrap intervals. |
| Where it stands | Project finding that challenges the proposal's starting assumption: rainfall and temperature, including archived numerical forecasts, added no measurable skill once radar history and orbit geometry were in the model. |
| Sources | [Vanderhoof et al. 2023, Remote Sensing of Environment 288:113498](https://doi.org/10.1016/j.rse.2023.113498) |

### G4. Seven-day and 14-day horizons

| | |
|---|---|
| What existing studies have done | Larval development sets the useful lead time: pupation after about 8 days for An. arabiensis and 10 to 13 days for An. funestus in Tanzanian field habitats (Msugupakulya et al. 2024). |
| Their limitations | Forecast horizons in the literature are rarely tied to the treatment window. |
| The unresolved gap | **How much skill is lost between 7 and 14 days, and whether a 14-day lead is usable.** |
| How RINDA addresses it | Evaluates and displays the two horizons separately and warns that 14-day forecasts are weaker. |
| Research objective | Objective 1 · RQ1 |
| How the contribution will be evaluated | Separate PR-AUC, precision and recall per horizon on identical periods. |
| Where it stands | Project finding: 14-day alert precision missed its 0.75 target (0.72), and on February to September 2026 the retrained model was no better than the frozen one (0.674 against 0.677). |
| Sources | [Msugupakulya et al. 2024, Scientific Reports 14:1002](https://doi.org/10.1038/s41598-024-51152-1) |

### G5. Field larval verification joined to remote-sensing predictions

| | |
|---|---|
| What existing studies have done | Drone imagery finds water bodies well, but in one study only 31.9% of confirmed water bodies contained larvae (Francisco et al. 2026). Drone-visible habitat features were associated with larval presence in Malawi (Stanton et al. 2021). |
| Their limitations | In the review, 2 of 140 studies verified targets in the field. Programme surveys are not random and record positives more than negatives. |
| The unresolved gap | **Predictions are seldom checked by dipping at the predicted place, with absences recorded.** |
| How RINDA addresses it | Inspection tasks from forecasts, an offline field form that separates 'not sampled' from 'none found', supervisor review, and a dossier that keeps predicted, observed and confirmed evidence apart. |
| Research objective | Objective 1 (Study 2b) and Objective 3 (Study 3 positivity sample) |
| How the contribution will be evaluated | Share of confirmed water bodies with Anopheles larvae by habitat type, from a random sample chosen independently of the model. |
| Where it stands | Built, awaiting field data. RBC entomology team requested, not confirmed. |
| Sources | [Francisco et al. 2026, Scientific Reports 16:27916](https://doi.org/10.1038/s41598-026-58240-4); [Stanton et al. 2021, Malaria Journal 20:244](https://doi.org/10.1186/s12936-021-03759-2) |

### G6. IoT sensing as a ground reference

| | |
|---|---|
| What existing studies have done | Low-cost water-level sensors and LoRa links are mature (Ding et al. 2025; Dao et al. 2025; Kabi et al. 2023). |
| Their limitations | They are used for hydrology, not to check when a satellite first sees water or to calibrate water thresholds. |
| The unresolved gap | **Satellite water labels lack a ground reference for presence and onset.** |
| How RINDA addresses it | Authenticated telemetry endpoint, node registry, stored readings with units and range flags, delivery ratio, and a sensor layer on the map. No node is commissioned. |
| Research objective | Objective 2 · RQ4 · H4 |
| How the contribution will be evaluated | Paired F1 difference between thresholds calibrated on sensor data and on Sentinel-2, each node held out in turn; detection delay described. |
| Where it stands | Interface built, awaiting hardware in the field. |
| Sources | [Ding et al. 2025, Water Research X 27:100298](https://doi.org/10.1016/j.wroa.2024.100298); [Dao et al. 2025, IEEE Sensors Journal 25(8):13175-13188](https://doi.org/10.1109/jsen.2025.3533014); [Kabi et al. 2023, HardwareX 14:e00414](https://doi.org/10.1016/j.ohx.2023.e00414) |

### G7. Compute limits and edge deployment

| | |
|---|---|
| What existing studies have done | Networks have been benchmarked on the Jetson Orin Nano, where the steps before and after inference take a large share of total time (Szegedi et al. 2026). Lightweight water segmentation exists (Cao et al. 2025), and a quantised detector has run on a drone FPGA (Rosi et al. 2026). |
| Their limitations | Models are usually chosen by FLOPs or inference-only latency, and by averages, not by the slowest runs of the whole pipeline. |
| The unresolved gap | **Whether choosing by measured 99th-percentile full-pipeline latency gives a different and better model than proxy measures.** |
| How RINDA addresses it | Flight-log analytics (latency distribution, deadline-miss ratio, stage tails). Records size, inference time and memory for every trained forecast model on the server. The onboard water model does not exist yet. |
| Research objective | Objective 3 · RQ2 · H2 |
| How the contribution will be evaluated | Deadline-constrained F1 on untouched data for configurations chosen by p99 latency, FLOPs and inference-only latency. |
| Where it stands | Awaiting the onboard model and Jetson measurements. Server timings in RINDA are not edge measurements. |
| Sources | [Szegedi, Bukschat and Vetter 2026, IEEE Access 14:107765-107785](https://doi.org/10.1109/access.2026.3712902); [Cao et al. 2025, IEEE JSTARS 18:10608-10624](https://doi.org/10.1109/jstars.2025.3552783); [Rosi et al. 2026, EAI Endorsed Transactions on IoT 11](https://doi.org/10.4108/eetiot.12911) |

### G8. From habitat intelligence to targeted drone larviciding

| | |
|---|---|
| What existing studies have done | Drones already apply Bti in Rwandan rice fields, with a 95.3% fall in Anopheles larval density in a non-randomised trial (Munyakanage et al. 2024). Drone mapping with image analysis found over three times as many sites and cut larvicide use by more than 60% in Ghana (Bokpin et al. 2026). Lag compensation reduced mean spray lag from 3.87 m to 0.32 m (Wang et al. 2023). |
| Their limitations | Mapping and treatment are separate passes, a day or more apart (Bokpin et al. 2026; Hardy et al. 2023). Compensation uses average delays; an RTK system still missed target starts and ends by about 0.9 m (Lyu et al. 2025). |
| The unresolved gap | **No system links a forecast to a mission, and no validated method predicts whether a camera-triggered spray is on time and on target (the core of the thesis).** |
| How RINDA addresses it | Inspection flights from forecasts; treatment missions only from supervisor-authorised habitats; approval, exports without spray commands, flight results. The timing-and-placement controller is studied outside RINDA; RINDA ingests its logs. |
| Research objective | Objective 2 (mission link) and Objective 3 · RQ6 · H6a to H6c (core) |
| How the contribution will be evaluated | Calibration of predicted treatment probability; share of eligible targets treated with controller C3 against C2; non-inferior off-target fraction. |
| Where it stands | Mission workflow built. Controller hypotheses untested. |
| Sources | [Munyakanage et al. 2024, Malaria Journal 23:281](https://doi.org/10.1186/s12936-024-05104-9); [Bokpin et al. 2026, PLOS One 21(2):e0340690](https://doi.org/10.1371/journal.pone.0340690); [Hardy et al. 2023, Malaria Journal 22:286](https://doi.org/10.1186/s12936-023-04713-0); [Wang et al. 2023, Journal of Agricultural Engineering](https://doi.org/10.4081/jae.2023.1547); [Lyu et al. 2025, Agriculture 15(19):2070](https://doi.org/10.3390/agriculture15192070); [Cazorla et al. 2019, ACM Computing Surveys 52(1)](https://doi.org/10.1145/3301283); [Friebe et al. 2024, Real-Time Systems 60(3):443-490](https://doi.org/10.1007/s11241-024-09431-7) |

### G9. Operational effectiveness of targeted interventions

| | |
|---|---|
| What existing studies have done | Larviciding reduces vectors when enough productive habitats are found and treated; in the review, 9 studies measured an intervention outcome. |
| Their limitations | In the review, 1 of 140 studies delivered a targeted intervention from a prediction. In Ghana, malaria case trends did not differ between study arms (Bokpin et al. 2026). |
| The unresolved gap | **Whether forecast-guided treatment reaches new habitats sooner than a two-pass workflow.** |
| How RINDA addresses it | Indicators from records only: inspection and treatment turnaround, follow-up completion, habitats positive more than once, coverage of positive habitats. Malaria incidence is not measured. |
| Research objective | Objective 3 context (Study 5, with RBC) |
| How the contribution will be evaluated | Time from water onset to confirmed treatment, analysed as interval-censored; share treated before late instars; cost per positive habitat treated. |
| Where it stands | Awaiting an approved study with RBC. Probably an estimation study, not a trial of health impact. |
| Sources | [Bokpin et al. 2026, PLOS One 21(2):e0340690](https://doi.org/10.1371/journal.pone.0340690) |

### G10. Data availability, resolution, generalisation and uncertainty

| | |
|---|---|
| What existing studies have done | Published models report accuracy on the area and period they were built in. |
| Their limitations | Random splits of correlated satellite samples overstate skill; few studies test a frozen model in a second area or publish calibrated uncertainty. |
| The unresolved gap | **Transfer without retraining, honest uncertainty, and labels that measure the intended thing.** |
| How RINDA addresses it | Temporal splits with an embargo, held-out 500 m blocks, calibration plots, conformal sets, a model registry where approval is explicit, and the audit warning on every forecast. |
| Research objective | Objective 2 · RQ3 · H3, and all objectives |
| How the contribution will be evaluated | Frozen model against B1 on identical site-days in a second area; coverage of prediction sets; label agreement with ground readings. |
| Where it stands | Project finding (audit): 83 to 87% of events lie within 100 m of the lake; Sentinel-1C verifies 73 to 80% of evaluation events and is not harmonised; 297 of 598 evaluated sites are outside the sector. A second area is not yet selected. |
| Sources | This project, 5 November 2026 audit |

## Assumptions the evidence has challenged

- **The label does not measure marshland water.** Corrected on 13 November 2026: once Sentinel-1C and 1D were put on the Sentinel-1A reference, almost all inland 'new water' disappeared and 98% of events lie at the lake shore. The forecast is a lake-shore forecast; at 50 m with these radar thresholds new inland water is barely detected. *Recommended:* Describe the served forecast as lake-shore only. For the marshland, design a label for small or vegetated water and check it against in-situ sensors (RQ4) and field visits.
- **Weather did not help.** The proposal expected rainfall to drive formation. In this valley the model's skill came from radar history, orbit geometry and terrain. This fits managed or lake-driven water more than rain-fed pools. *Recommended:* State this finding plainly in Paper 1 and test it again on inland sites after relabelling.
- **A simple baseline is hard to beat.** The first model lost to a 30-day water-frequency rule. Later versions beat it, but partly on data that was not blind. *Recommended:* Register the forecast archive before outcomes are known, as H1 requires, and keep the baseline on every screen.
- **Programme records are not a random sample.** RBC surveys go where staff expect larvae, with varying dipping effort. A habitat model trained on them describes where RBC looks. *Recommended:* Model detection effort, and validate on the random dipping samples of Studies 3 and 5 before any water body is skipped.
- **The forecast model is not the edge model.** The forecast runs on a ground server. Latency, memory and power on the Jetson concern the onboard water-segmentation model and the spray controller, which are not built yet. *Recommended:* Keep server timings and edge measurements apart, as RINDA now does.
- **Sites were sampled by water frequency.** Monitoring locations were chosen using water frequency in a box around the sector, so about half lie outside it and few are on the valley floor. *Recommended:* Define the study population for the marshland explicitly, with official boundaries from NISR or RBC.
