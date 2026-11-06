# User guide

RINDA forecasts whether **new surface water** will appear at each of 600 monitoring points (50 m circles) in Gikomero
Sector, 7 or 14 days ahead. A forecast is a reason to inspect, never proof of larvae and never a reason to treat on
its own.

## 1. Read the situation (everyone)

Open **Situation overview**. Read the status strip from left to right:

- **Forecast issued**: the issue date and whether it is the latest.
- **Valid for**: the window in which the forecast can be checked (target date ± 2 days).
- **Freshness**: *Current* or *Stale*. A stale forecast describes the past; do not plan today's work from it.
- **Inputs at issue**: the last radar pass and weather day the forecast used.
- **Model**: *Research stage*. Read the banner's "Why" for the reasons.

The four tiles count points by **verification priority**: High, Moderate (not configured yet), Insufficient evidence,
Lower. The map shows the same with shapes as well as colours: triangles are High, dashed rings are Insufficient
evidence, small dots are Lower.

## 2. Inspect a point

Click any point on a map or table row. The dossier shows the priority and why, the probability and its uncertainty,
the recommended action, the forecast record (dates, inputs, model), field evidence at that point, the radar history,
and which environmental factors drove the forecast. Zoom in past level 15 to see each point's 50 m circle.

## 3. Supervisors: alerts and inspections

**Field surveillance → Alerts** lists every High-priority point for the selected issue. For each alert you can:

- **Assign inspection**: choose a field officer and a due date before the target date.
- **Acknowledge**: seen, no task yet.
- **Dismiss**: needs a reason (for example "inspected yesterday").

Expired alerts cannot be acted on. On the demonstration site you can "Assign exercise" to practise.

## 4. Field officers: record a finding

**Field surveillance → Inspections** shows your tasks. Press **Start**, then **Record finding**.

- Tap **Use device GPS** at the water's edge.
- Water present: yes / no / could not access.
- Larvae: **Found**, **Sampled, none found** (record the number of dips) or **Not sampled**. Never choose "none
  found" if you did not dip.
- Add the count, *Anopheles* presence, species if identified, stages, pupae, depth and vegetation if known.

Without a connection, the record is saved on the phone and sent automatically later; the top bar shows how many are
queued. Sending the same record twice never creates a duplicate.

Records far from the point, without GPS, or with poor GPS accuracy go to review with a flag.

**A habitat that is not one of the monitoring points.** Tick **New habitat, not a monitoring point**, tap **Use device
GPS** at the water's edge and describe the place. RINDA registers it with its own code (H0001, H0002, ...). From then
on it can be inspected again, reviewed, treated and followed up like any point; type its code in the point field.
Habitats carry no forecast.

Record **habitat permanence**, **sun exposure** and **distance to the nearest house** when you can. RINDA works out
**larvae per dip** (larvae counted ÷ dips) and shows it as you type.

**Working without a connection.** Open RINDA once while connected and sign in. After that, the console and your
inspection list open on the phone without signal. Sign out on a shared phone: this deletes the saved copy.

**Language.** **EN / RW** in the top bar switches the navigation and field screens to Kinyarwanda. The Kinyarwanda is
a draft: report unclear words through Help & Glossary → Feedback.

## 5. Supervisors: review and authorise

**Review queue**: open a record, then **Verify**, **Return for correction** or **Reject** (a reason is required for
the last two). You cannot verify your own records.

From a verified record with larvae (or water present but not yet sampled), **Authorise intervention**: choose the
method, product and dose, and enter the protocol reference. The person treating then records **quantity and time**
under **Interventions**. Close each intervention with a later verified observation at the same point and an outcome.

## 6. Check the forecast against reality

**Replay & validation**: pick any past issue with the date selector. You see what was forecast, which inputs it had,
and what the next radar pass found: hits, false alarms, points flagged as insufficient evidence, and misses. Compare
7-day and 14-day performance across the whole test period. Analysts can export the summary.

## 7. Reports

**Reports & bulletins** produce a one-page A4 bulletin for any issue, stamped with its status. Supervisors can export
tasks, observations and interventions as CSV from **Field surveillance**; demonstration records are labelled.

## 8. Monitoring, weekly report and messages (supervisors, programme managers)

**Monitoring & Reporting → Indicators** shows how RINDA is used, by RE-AIM dimension. Every figure is counted from
records. "No data" means there are no records yet, not zero. "Few records" warns that a share rests on fewer than 20
records. The page does not show malaria cases.

**Weekly report & DHIS2**: choose the epidemiological week. **Report** opens a printable A4 summary in English or
draft Kinyarwanda, with signature lines. The DHIS2 CSV or JSON is for the HMIS team. It stays marked *mapping
pending* until RBC supplies the DHIS2 identifiers, and RINDA never sends it anywhere itself.

**Messages** (supervisors): assigning an inspection prepares an SMS notice to the officer in their language.
**Prepare weekly digest** writes a short summary for each supervisor and programme manager. No SMS or WhatsApp
service is connected, so copy the text, send it from the programme phone and press **Mark sent**.

## 9. Help

**Help & glossary** has a searchable glossary, this guide in short form, the introduction tour and a feedback form.
