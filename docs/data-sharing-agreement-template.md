# Data-sharing agreement: template

**DRAFT TEMPLATE. Not legal advice.** A starting point for the University of Rwanda (ACEIoT), RBC Malaria & NTD
Division and any other parties to agree how data flows in and out of RINDA during a controlled evaluation. It must be
reviewed by each institution's legal or data-protection officer, and checked against Law No. 058/2021 on the
protection of personal data and privacy, and any guidance from the National Cyber Security Authority (NCSA).

Square brackets mark items the parties must fill in.

## 1. Parties and roles

| Party | Role under Law 058/2021 | Contact |
|---|---|---|
| [RBC Malaria & NTD Division] | [Data controller for operational field records] | [name, email] |
| [University of Rwanda, ACEIoT] | [Processor for operations; controller for research datasets derived under ethics approval] | [name, email] |
| [Hosting provider, if any] | [Sub-processor] | [name, email] |

## 2. Purpose

Controlled evaluation of RINDA for larval source management in [Gikomero Sector, Gasabo District] from [start] to
[end]. RINDA is ready for controlled testing and is not validated for intervention decisions. Data are used to:
(a) run inspection, review and treatment workflows; (b) evaluate the system with RE-AIM/CFIR measures; (c) validate
the habitat forecast against field observations. No other use without a written amendment.

## 3. Data covered

| Category | Examples | Personal data? | Held where |
|---|---|---|---|
| Staff accounts | Name, work email, role, district, phone (optional), language, sign-in times | Yes | RINDA database |
| Field records | GPS of habitats, larval counts, species, treatments, reviewer and observer IDs | Indirectly (linked to staff) | RINDA database |
| Audit trail | Who did what and when | Yes | RINDA database |
| Messages | Text of task notices and digests, recipient | Yes | RINDA outbox |
| Forecasts and satellite data | Monitoring-point probabilities | No | Study bundle |
| Household or patient data | — | **Not collected.** RINDA must not be used to record them | — |

## 4. Rules

1. **Access:** by role, as configured in RINDA (`docs/admin-guide.md`). The programme-manager role sees aggregates
   only. Accounts are named, never shared.
2. **Location:** data are stored on [RBC / national data centre] servers in Rwanda. Transfers outside Rwanda need
   [the controller's written approval and the conditions of Law 058/2021, Articles on cross-border transfer].
3. **Retention:** audit [5 years]; field records [per programme policy]; staff accounts [until deactivated + 1
   year]; outbox messages [1 year]; demonstration records deleted at the end of the evaluation.
4. **Research use:** de-identified extracts (no staff names, emails or phones) may be used for the PhD under
   [ethics approval number]. Publications report aggregates only.
5. **External systems:** weekly aggregates may be exported to DHIS2 by the HMIS team. No record-level data leave
   RINDA without [the controller's approval].
6. **Messaging:** if an SMS or WhatsApp provider is connected, only work messages (task notices, digests) are sent,
   to staff who have consented, with no larval records or GPS in the text.
7. **Breaches:** notify [contact] within [48 hours] of discovery; the controller notifies the supervisory authority
   as the law requires.
8. **End of agreement:** the processor returns or deletes data within [30 days] and confirms in writing.

## 5. Sign-off

| Party | Name | Title | Signature | Date |
|---|---|---|---|---|
| | | | | |
