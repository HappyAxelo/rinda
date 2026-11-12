// Interface language. English is the reference text; Kinyarwanda strings are a
// DRAFT prepared for terminology sessions with RBC and district teams (backlog
// B-11) and must be reviewed before field use. A string with no Kinyarwanda entry
// falls back to English, so a partial dictionary never hides information.
//
// Usage: t('Record observation'). The English text is the key.

const RW = {
  // navigation (six modules)
  'National Situation Overview': 'Uko ibintu bihagaze mu gihugu', 'National Surveillance Map': "Ikarita y'igenzura",
  'Field Surveillance & Operations': "Igenzura n'ibikorwa mu murima", 'Drone Operations': 'Ibikorwa bya drone',
  'Monitoring, Impact & Reporting': 'Ikurikirana na raporo', 'Administration & Evidence': "Ubuyobozi n'ibimenyetso",
  Operations: 'Ibikorwa', Evidence: 'Ibimenyetso', Programme: 'Porogaramu',
  'Situation Overview': 'Uko ibintu bihagaze', 'Habitat Forecast': "Iteganyagihe ry'indiri",
  'Field Surveillance': 'Igenzura mu murima', 'Mission Planning': "Gutegura ingendo za drone",
  'Flight Operations': "Ibikorwa bya drone", 'Replay & Validation': "Isubiramo n'igenzura",
  'Model Assurance': "Ubwizerwe bw'icyitegererezo", 'Scenario Lab': "Igerageza ry'ibihe",
  'Field Network': "Ibikoresho byo mu murima", 'Impact & Economics': "Ingaruka n'ubukungu",
  'Monitoring & Reporting': 'Ikurikirana na raporo', 'Reports & Bulletins': "Raporo n'amatangazo",
  'Programme Evidence': 'Ibimenyetso bya porogaramu', 'Data & Provenance': "Amakuru n'inkomoko",
  Administration: 'Ubuyobozi bwa sisitemu', 'Help & Glossary': "Ubufasha n'inkoranyamagambo",
  'Sign in': 'Injira',
  // field surveillance
  'Field surveillance': 'Igenzura mu murima', Inspections: 'Igenzura', 'Record observation': 'Andika ibyabonetse',
  'Review queue': 'Ibitegereje kugenzurwa', Interventions: 'Ibikorwa byo gutera umuti', Alerts: 'Imiburo',
  Task: 'Igikorwa', Point: 'Ahantu', Priority: 'Icyihutirwa', 'From forecast': 'Biturutse ku iteganyagihe',
  'Assigned to': 'Yahawe', Due: 'Igihe ntarengwa', Status: 'Uko bihagaze', overdue: 'cyarengeje igihe',
  Start: 'Tangira', 'Record finding': 'Andika ibyabonetse', Cancel: 'Hagarika', Routine: 'Bisanzwe',
  'Assign inspection': 'Tanga igenzura',
  'Field observation': 'Ibyabonetse mu murima', 'Monitoring point': 'Ahagenzurwa',
  'New habitat, not a monitoring point': 'Indiri nshya, itari ahagenzurwa',
  'Habitat description': "Ibisobanuro by'indiri",
  'Task number': "Nimero y'igikorwa", optional: 'si ngombwa', 'Observed at (local time)': 'Igihe byabonetse (isaha yo mu Rwanda)',
  Position: 'Aho uherereye', 'Use device GPS': 'Koresha GPS ya telefone', 'No position yet': 'Nta hantu harabonekamo',
  'Water present': 'Hari amazi', Choose: 'Hitamo', Yes: 'Yego', No: 'Oya',
  'Could not access / unknown': 'Ntibyashobotse kugerayo / ntibizwi', 'Habitat type': "Ubwoko bw'indiri",
  Larvae: 'Larves', Found: 'Zabonetse', 'Sampled, none found': 'Hafashwe ibipimo, nta zabonetse',
  'Not sampled': 'Ntihafashwe ibipimo', 'Dips taken': 'Ibipimo byafashwe', 'Larvae counted': 'Larves zabaruwe',
  'Larvae per dip': 'Larves ku gipimo', 'Not identified': 'Ntibyamenyekanye', Present: 'Zirahari', Absent: 'Ntazihari',
  'Species identified': 'Amoko yamenyekanye', Stages: 'Ibyiciro', Pupae: 'Pupes', 'Pupae counted': 'Pupes zabaruwe',
  'Water depth': "Ubujyakuzimu bw'amazi", 'Vegetation cover': 'Ibimera bitwikiriye', Notes: 'Ibisobanuro',
  'Habitat permanence': 'Igihe indiri imara', Permanent: 'Ihoraho', 'Semi-permanent': 'Imara igihe',
  Temporary: "Iy'igihe gito", 'Sun exposure': 'Izuba', Sunlit: 'Ku zuba', 'Partly shaded': 'Igicucu gike',
  Shaded: 'Mu gicucu', 'Distance to nearest house': "Intera igera ku nzu ya hafi",
  'Submit observation': 'Ohereza ibyabonetse',
  '"Not sampled" is never treated as "none found".': '"Ntihafashwe ibipimo" ntibisobanura "nta zabonetse".',
  'Works offline: records are queued on this device and sent when the connection returns.':
    "Bikora nta murandasi: ibyanditswe bibikwa kuri iyi telefone bikoherezwa murandasi igarutse.",
  'Queued on this device': 'Bibitswe kuri iyi telefone', 'Send now': 'Ohereza ubu', Discard: 'Siba',
  'Register it with your GPS position; it gets its own habitat code for review, treatment and follow-up.':
    "Yandike ukoresheje GPS; ihabwa kode yayo kugira ngo igenzurwe, iterwe umuti kandi ikurikiranwe.",
  // shell
  'Draft translation': "Ubusobanuro bw'agateganyo",
};

// French has a slot so a dictionary can be added without touching any screen.
const DICTS = { en: {}, rw: RW, fr: {} };
let lang = 'en';
try { const s = localStorage.getItem('rinda.lang'); if (s === 'rw' || s === 'en') lang = s; } catch (e) { /* storage blocked */ }

export const getLang = () => lang;
export function setLang(l) {
  lang = l === 'rw' ? 'rw' : 'en';
  try { localStorage.setItem('rinda.lang', lang); } catch (e) { /* storage blocked */ }
  document.documentElement.lang = lang;
}
export const t = (s) => DICTS[lang][s] ?? s;
export const coverage = () => Object.keys(RW).length;
document.documentElement.lang = lang;
