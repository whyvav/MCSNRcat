"""Static-site generator for the LMC Supernova Remnant Catalog.

Reads the versioned extended catalog CSV (produced by VLMism
scripts/01_build_lmc_master.py) and emits a complete static website:

    site/
    ├── index.html            searchable/sortable census + sky map
    ├── about.html            classification criteria, history, citation
    ├── objects/<slug>.html   one page per object (Aladin Lite multiwavelength
    │                         viewer + properties + external services)
    ├── catalog.json          machine-readable download
    ├── catalog.csv           same, CSV
    └── style.css

Usage:
    python build.py --catalog data/lmc_snrs_extended_v2.csv --out site

No server-side code: hostable on GitHub Pages / any static host. Imagery is
streamed client-side from CDS HiPS via Aladin Lite v3 (no images stored here).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
from pathlib import Path
from string import Template

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SITE_NAME = "MCSNRcat^log"
SITE_CANONICAL_NAME = "MCSNRcatalog"
VERSION_NOTE = "Maintained by V. Shukla; assembled from the literature (see About)."

#: HiPS surveys offered in the per-object viewer. Entries are either CDS
#: registry IDs (verify at https://aladin.cds.unistra.fr/hips/list) or direct
#: HiPS base URLs (for maps not registered at CDS, e.g. the MPE-hosted
#: eROSITA DR1 HiPS). All verified working 2026-07-08 – see
#: VLMism/docs/DATA_SOURCING.md §4 for provenance.
ALADIN_SURVEYS = [
    ("CDS/P/DSS2/color", "DSS2 optical"),
    ("CDS/P/SHASSA/H", "SHASSA Hα"),
    ("https://erosita.mpe.mpg.de/dr1/erodat/static/hips/eRASS1_RGB_Rate_c010/",
     "eROSITA DR1 X-ray (RGB)"),
    ("ESDC/P/XMM/EPIC-RGB", "XMM-Newton EPIC (RGB)"),
    ("CSIRO/P/RACS/low/I", "RACS-low 888 MHz radio"),
    ("CDS/P/SUMSS", "SUMSS 843 MHz radio"),
    ("CDS/P/GALEXGR6/AIS/color", "GALEX UV"),
    ("CDS/P/allWISE/color", "AllWISE mid-IR"),
    ("CDS/P/2MASS/color", "2MASS near-IR"),
]

#: Cutout PNG bands shown on object pages (when images/<slug>/ exists),
#: in display order: (band suffix, label).
IMAGE_BANDS = [
    ("rgb", "Composite (R radio / G Hα / B X-ray)"),
    ("xray_soft", "eROSITA 0.2–2.3 keV"),
    ("halpha", "Hα (DeMCELS)"),
    ("sii", "[S II] (DeMCELS)"),
    ("sii_halpha_ratio", "[S II]/Hα ratio"),
    ("radio_888", "ASKAP 888 MHz"),
]

PROPERTY_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Identification", [
        ("id", "Catalog ID", ""),
        ("alias", "Alias / common name", ""),
        ("klass", "Status", ""),
        ("sn_type", "SN type", "('?' = tentative)"),
        ("ref_discovery", "Discovery ref", ""),
        ("ref_confirm", "Confirmation ref", ""),
    ]),
    ("Position (ICRS)", [
        ("ra", "RA [deg]", ""),
        ("dec", "Dec [deg]", ""),
    ]),
    ("Morphology (Zangrandi+24, Shukla 24)", [
        ("size_maj_arcmin", "Major axis [arcmin]", ""),
        ("size_min_arcmin", "Minor axis [arcmin]", ""),
        ("d_arcmin", "Mean diameter [arcmin]", ""),
        ("d_pc", "Diameter [pc]", "at 50 kpc"),
        ("pa_deg", "Position angle [deg]", ""),
        ("shape", "Fitted shape", ""),
        ("ovality", "Ovality", ""),
        ("eccentricity", "Eccentricity", ""),
    ]),
    ("X-ray (eROSITA / XMM)", [
        ("xray_rate_ctss", "eRASS rate [cts/s]", "(Zangrandi+24)"),
        ("xray_rate_err", "Rate error", "(Zangrandi+24)"),
        ("lx_1e35", "L_X [10^35 erg/s]", "(Maggi+16, 0.3-8 keV)"),
        ("nh_1e21", "N_H [10^21 cm^-2]", "(Maggi+16)"),
        ("age_kyr", "Age [kyr]", "(Maggi+16)"),
    ]),
    ("Radio (Bozzetto+17)", [
        ("alpha_radio", "Spectral index α", ""),
        ("alpha_radio_err", "α error", ""),
        ("s_1ghz_jy", "S_1GHz [Jy]", ""),
    ]),
    ("Energetics (Leahy 17)", [
        ("e0_1e51_erg", "E_0 [10^51 erg]", ""),
        ("age_l17_yr", "Age [yr]", ""),
        ("n0_cm3", "n_0 [cm^-3]", ""),
    ]),
]

PAGE = Template("""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<link rel="stylesheet" href="$root/style.css">
<link rel="icon" href="$root/brand/favicon.svg" type="image/svg+xml">
$head_extra
</head><body>
<header class="site-header">
  <a class="brand-lockup" href="$root/index.html" aria-label="$site_name home">
    <img src="$root/brand/logo-mark.svg" alt="" class="brand-mark" width="80" height="55">
    <span class="brand-copy">
      <span class="brand-word">MCSNRcat<span aria-hidden="true">^</span>log</span>
      <span class="brand-tagline">Magellanic Cloud Supernova Remnant Catalog</span>
    </span>
  </a>
  <nav class="site-nav" aria-label="Primary">
    <a href="$root/index.html">Census</a>
    <a href="$root/about.html">About</a>
    <a href="$root/catalog.csv">CSV</a>
    <a href="$root/catalog.json">JSON</a>
  </nav>
  <span class="ver">Data version: <code>$version</code></span>
</header>
<main>$body</main>
<footer class="site-footer">
  <span>$version_note Data version: <code>$version</code>.</span>
  <span>Imagery: CDS Aladin Lite / HiPS.</span>
  <span>Built from the <a href="https://github.com/whyvav/MCSNRcat">source on GitHub</a>.</span>
</footer>
</body></html>""")


#: Columns the generator and downstream consumers rely on.
REQUIRED_COLUMNS = ["snr_key", "id", "name", "ra", "dec", "klass"]
VALID_KLASSES = {"SNR", "SNR_candidate"}


def validate_catalog(df: pd.DataFrame) -> None:
    """Fail fast on structural problems in the source catalog.

    Enforces the invariants the site (and VLMism) depend on, so a hand-edit
    typo is caught at build time instead of silently shipping a broken page.
    Raises ``ValueError`` listing every problem found.
    """
    errors: list[str] = []

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"catalog missing required columns: {missing}")

    bad_klass = sorted(set(df["klass"]) - VALID_KLASSES)
    if bad_klass:
        errors.append(f"unknown klass values: {bad_klass} (allowed: {sorted(VALID_KLASSES)})")

    for col in ("snr_key", "id", "name"):
        dupes = df[col][df[col].duplicated()].tolist()
        if dupes:
            errors.append(f"duplicate {col}: {dupes}")

    for _, r in df.iterrows():
        if r["klass"] == "SNR" and not str(r["id"]).startswith("MCSNR "):
            errors.append(f"{r['snr_key']}: confirmed SNR id {r['id']!r} must start with 'MCSNR '")
        if r["klass"] == "SNR_candidate" and str(r["id"]) != str(r["name"]):
            errors.append(f"{r['snr_key']}: candidate id {r['id']!r} must equal name {r['name']!r}")

    for col in ("ra", "dec"):
        if not pd.to_numeric(df[col], errors="coerce").notna().all():
            errors.append(f"non-numeric values in {col}")

    if errors:
        raise ValueError("catalog validation failed:\n  - " + "\n  - ".join(errors))
    logger.info("catalog validation passed: %d objects, no structural errors", len(df))


def latest_catalog(data_dir: Path = Path("data")) -> Path:
    """Return the highest-versioned ``lmc_snrs_extended_v*.csv`` in ``data_dir``."""
    cands = sorted(
        data_dir.glob("lmc_snrs_extended_v*.csv"),
        key=lambda p: int(p.stem.rsplit("_v", 1)[-1]),
    )
    if not cands:
        raise FileNotFoundError(f"no lmc_snrs_extended_v*.csv found in {data_dir}")
    return cands[-1]


def slugify(obj_id: str) -> str:
    return obj_id.replace(" ", "_").replace("/", "-")


#: Matches `^exponent` (e.g. "10^35", "cm^-2") and `_subscript` (e.g. "L_X",
#: "S_1GHz") tokens in property labels/notes so they render as real
#: super/subscripts instead of literal carets and underscores.
_SUPERSCRIPT_RE = re.compile(r"\^(-?[A-Za-z0-9.]+)")
_SUBSCRIPT_RE = re.compile(r"_([A-Za-z0-9.]+)")


def mathify(text: str) -> str:
    text = _SUPERSCRIPT_RE.sub(r"<sup>\1</sup>", text)
    text = _SUBSCRIPT_RE.sub(r"<sub>\1</sub>", text)
    return text


def fmt(v: object, nd: int = 3) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{nd}g}"
    return str(v)


def load_image_manifest(images_dir: Path) -> dict[str, dict]:
    """Index the pipeline-generated cutout PNGs by object slug.

    Expects the layout written by VLMism ``scripts/04_build_snr_images.py``:
    ``images/<slug>/<slug>_<band>.png`` plus ``images/manifest.csv``. Returns
    ``{slug: {band: {"file": ..., "survey": ..., "viz_grade": bool}}}``.
    Missing directory → empty dict (the site builds fine without images).
    """
    out: dict[str, dict] = {}
    manifest = images_dir / "manifest.csv"
    if not manifest.exists():
        if images_dir.exists():
            logger.warning("%s exists but has no manifest.csv — ignoring", images_dir)
        return out
    df = pd.read_csv(manifest)
    for _, r in df.iterrows():
        slug_map = out.setdefault(str(r["slug"]), {})
        slug_map[str(r["band"])] = {
            "file": str(r["file"]),
            "survey": str(r.get("survey", "")),
            "viz_grade": str(r.get("viz_grade", "")).lower() == "true",
        }
    logger.info("image manifest: %d objects with cutout PNGs", len(out))
    return out


def images_panel(slug: str, obj_images: dict) -> str:
    """HTML for the multiwavelength cutout strip of one object page."""
    cards = ""
    for band, label in IMAGE_BANDS:
        entry = obj_images.get(band)
        if not entry:
            continue
        tag = '<span class="viz">quick-look</span>' if entry["viz_grade"] else ""
        cards += f"""<figure>
  <a href="../images/{entry['file']}" target="_blank">
    <img src="../images/{entry['file']}" loading="lazy" alt="{slug} {label}"></a>
  <figcaption>{label}{tag}<span class="note">{entry['survey']}</span></figcaption>
</figure>"""
    if not cards:
        return ""
    return f"""
<section class="cutouts"><h3>Multiwavelength cutouts</h3>
<div class="cutgrid">{cards}</div>
<p class="note">Pipeline-generated cutouts (asinh stretch). "quick-look" =
hips2fits fallback, visualization grade only — do not measure fluxes on
these. Provenance: <a href="../images/manifest.csv">images/manifest.csv</a>;
pipeline: <a href="https://github.com/whyvav/VLMism">VLMism</a>.</p>
</section>"""


def object_page(row: pd.Series, version: str,
                obj_images: dict | None = None) -> str:
    fov = max(3.0 * (row.get("d_arcmin") or 4.0) / 60.0, 0.12)
    surveys_js = json.dumps([s for s, _ in ALADIN_SURVEYS])
    options = "".join(
        f'<option value="{sid}">{label}</option>' for sid, label in ALADIN_SURVEYS
    )
    groups_html = ""
    for gname, fields in PROPERTY_GROUPS:
        rows_html = ""
        for key, label, note in fields:
            raw = row.get(key)
            val = fmt(raw)
            if (key == "xray_rate_ctss" and row.get("xray_rate_is_upper_limit")
                    and not (isinstance(raw, float) and np.isnan(raw))):
                val = f"&lt; {val}"
            note_html = f'<span class="note">{mathify(note)}</span>' if note else ""
            rows_html += f"<tr><th>{mathify(label)}{note_html}</th><td>{val}</td></tr>"
        groups_html += f"<section><h3>{mathify(gname)}</h3><table>{rows_html}</table></section>"

    thesis_note = row.get("thesis_note")
    banner = (
        f'<p class="banner">{thesis_note}</p>' if isinstance(thesis_note, str) else ""
    )
    ra, dec = row["ra"], row["dec"]
    body = f"""
<header class="page-head object-head">
  <div>
    <h1>{row['id']}</h1>
    <p class="lede">{fmt(row.get('alias'))}</p>
  </div>
  <span class="status-pill {'snr' if row['klass'] == 'SNR' else 'cand'}">{row['klass'].replace('_', ' ')}</span>
</header>
{banner}
<div class="objgrid">
  <section class="viewer-panel" aria-label="Multiwavelength sky viewer">
    <div id="aladin" style="width:100%;height:420px"></div>
    <div class="controls">
      <label>Survey <select id="survey">{options}</select></label>
      <span class="note">FoV {fov:.2f}° · drag / scroll to explore</span>
    </div>
    <div class="linkrow">
      <a href="https://simbad.cds.unistra.fr/simbad/sim-coo?Coord={ra}+{dec}&Radius=2&Radius.unit=arcmin" target="_blank">SIMBAD</a>
      <a href="https://sky.esa.int/esasky/?target={ra}%20{dec}&fov={fov:.2f}&sci=true" target="_blank">ESASky</a>
      <a href="https://ui.adsabs.harvard.edu/search/q=%22{row['name']}%22%20OR%20%22{row['id']}%22" target="_blank">ADS search</a>
      <a href="https://vizier.cds.unistra.fr/viz-bin/VizieR-4?-c={ra}%20{dec}&-c.rm=2" target="_blank">VizieR cone</a>
    </div>
  </section>
  <div class="property-stack">{groups_html}</div>
</div>
{images_panel(slugify(row["id"]), obj_images or {})}
<script src="https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js" charset="utf-8"></script>
<script>
A.init.then(() => {{
  const aladin = A.aladin("#aladin", {{
    target: "{ra} {dec}", fov: {fov:.3f}, survey: "{ALADIN_SURVEYS[0][0]}",
    showFullscreenControl: true, showLayersControl: false, cooFrame: "ICRSd",
  }});
  aladin.addCatalog(A.catalogFromSimbad({{ra: {ra}, dec: {dec}}}, {fov / 2:.3f}, {{shape: "circle", color: "#7dd3fc", onClick: "showPopup"}}));
  document.getElementById("survey").onchange = e => aladin.setImageSurvey(e.target.value);
}});
</script>"""
    return PAGE.substitute(
        title=f"{row['id']} — {SITE_NAME}", root="..", site_name=SITE_NAME,
        body=body, version=version, version_note=VERSION_NOTE, head_extra="",
    )


def index_page(df: pd.DataFrame, version: str) -> str:
    records = json.loads(df.replace({np.nan: None}).to_json(orient="records"))
    for r in records:
        r["slug"] = slugify(r["id"])
    n_snr = int((df["klass"] == "SNR").sum())
    n_cand = int((df["klass"] == "SNR_candidate").sum())
    body = Template("""
<section class="hero">
  <div>
    <h1>Supernova remnants in the Large Magellanic Cloud</h1>
    <p class="lede">A living, multiwavelength, literature-consolidated census of all known LMC SNRs. Click any object for a multiwavelength viewer, physical properties, and external archive links.</p>
  </div>
  <div class="stat-grid" aria-label="Catalog summary">
    <span class="stat-card"><strong>$n_snr</strong><span>confirmed SNRs</span></span>
    <span class="stat-card cand"><strong>$n_cand</strong><span>candidates</span></span>
    <span class="stat-card total"><strong>$n_total</strong><span>total objects</span></span>
  </div>
</section>
<section class="workbench" aria-label="Catalog workbench">
  <div id="controls">
    <label class="search-field"><span>Search</span><input id="q" placeholder="ID, alias, reference..." size="28"></label>
    <label><span>Status</span><select id="fclass"><option value="">All</option><option value="SNR">Confirmed</option><option value="SNR_candidate">Candidate</option></select></label>
    <label><span>Type</span><select id="ftype"><option value="">All</option><option>Ia</option><option>Ia?</option><option>CC</option><option>CC?</option></select></label>
    <button type="button" id="clear">Clear</button>
    <span class="count" id="count"></span>
  </div>
  <div id="wrap">
    <section id="skybox" aria-labelledby="sky-title">
      <div class="panel-head"><h2 id="sky-title">LMC sky map <span>ICRS</span></h2></div>
      <svg id="sky" width="460" height="430" role="img" aria-label="LMC SNR sky distribution"></svg>
      <div class="map-legend"><span><i class="dot snr"></i>Confirmed</span><span><i class="dot cand"></i>Candidate</span></div>
      <p class="note">RA increases leftward. Marker size follows angular radius.</p>
    </section>
    <section id="tablebox" aria-label="Sortable object table"><table id="tbl"><thead></thead><tbody></tbody></table></section>
  </div>
</section>
<section class="coverage" aria-label="Multiwavelength coverage">
  <h2>Multiwavelength coverage</h2>
  <div><strong>X-ray</strong><span>eROSITA, XMM-Newton</span></div>
  <div><strong>Optical</strong><span>DSS2, SHASSA H-alpha, DeMCELS</span></div>
  <div><strong>Radio</strong><span>RACS, SUMSS, ASKAP</span></div>
  <div><strong>UV / IR</strong><span>GALEX, AllWISE, 2MASS</span></div>
</section><script>
const DATA = $data;
const COLS = [
 {key:"id",label:"ID"},{key:"klass",label:"Status"},{key:"sn_type",label:"Type"},
 {key:"ra",label:"RA (deg)"},{key:"dec",label:"Dec (deg)"},{key:"r_arcmin",label:"r (')"},
 {key:"d_pc",label:"D (pc)"},{key:"alpha_radio",label:"alpha radio"},
 {key:"age_kyr",label:"Age (kyr)"},{key:"alias",label:"Alias"},{key:"ref_discovery_code",label:"Ref"}];
let sortKey="ra", sortAsc=true;
const fmt=(v,k)=>v==null?"":(typeof v==="number"&&!["ra","dec"].includes(k)?+v.toFixed(2):(typeof v==="number"?+v.toFixed(4):v));
const statusLabel=v=>v==="SNR"?"confirmed":"candidate";
const cell=(d,c)=>{
  if(c.key==="id") return `<td><a href="objects/$${d.slug}.html">$${d.id}</a></td>`;
  if(c.key==="klass") return `<td><span class="status-pill $${d.klass==="SNR"?"snr":"cand"}">$${statusLabel(d.klass)}</span></td>`;
  if(c.key==="sn_type" && d[c.key]) return `<td><span class="type-pill">$${d[c.key]}</span></td>`;
  return `<td>$${fmt(d[c.key],c.key)}</td>`;
};
// Sorting by "id" ignores the "MCSNR " prefix (via the bare-coordinate `name`
// field) so confirmed and candidate SNRs interleave by sky position instead
// of confirmed objects all sorting after candidates.
const sortVal=(d,k)=>k==="id"?d.name:d[k];
function filtered(){
  const q=document.getElementById("q").value.toLowerCase();
  const fc=document.getElementById("fclass").value, ft=document.getElementById("ftype").value;
  return DATA.filter(d=>(!fc||d.klass===fc)&&(!ft||d.sn_type===ft)&&
    (!q||[d.id,d.alias,d.ref_discovery_code,d.name].join(" ").toLowerCase().includes(q)));
}
function render(){
  let rows=filtered().slice().sort((a,b)=>{
    const va=sortVal(a,sortKey),vb=sortVal(b,sortKey);
    if(va==null)return 1; if(vb==null)return -1;
    return (va>vb?1:va<vb?-1:0)*(sortAsc?1:-1);});
  document.getElementById("count").textContent=rows.length+" objects";
  document.querySelector("#tbl thead").innerHTML="<tr>"+COLS.map(c=>
    `<th data-k="$${c.key}">$${c.label}$${sortKey===c.key?(sortAsc?" ▲":" ▼"):""}</th>`).join("")+"</tr>";
  document.querySelector("#tbl tbody").innerHTML=rows.map(d=>"<tr>"+COLS.map(c=>cell(d,c)).join("")+"</tr>").join("");
  document.querySelectorAll("#tbl th[data-k]").forEach(th=>th.onclick=()=>{
    const k=th.dataset.k;
    if(sortKey===k)sortAsc=!sortAsc; else {sortKey=k;sortAsc=true;}
    render();});
  drawSky(rows);
}
function drawSky(rows){
  const svg=document.getElementById("sky");
  const W=460,H=430,P=34;
  const ras=DATA.map(d=>d.ra),decs=DATA.map(d=>d.dec);
  const r0=Math.min(...ras)-.5,r1=Math.max(...ras)+.5;
  const d0=Math.min(...decs)-.3,d1=Math.max(...decs)+.3;
  const x=ra=>P+(r1-ra)/(r1-r0)*(W-2*P), y=de=>H-P-(de-d0)/(d1-d0)*(H-2*P);
  let s="";
  for(let g=Math.ceil(r0/2)*2;g<=r1;g+=2)
    s+=`<line x1="$${x(g)}" y1="$${P}" x2="$${x(g)}" y2="$${H-P}" stroke="var(--grid)"/>`+
       `<text x="$${x(g)}" y="$${H-P+14}" fill="var(--muted)" font-size="10" text-anchor="middle">$${g} deg</text>`;
  for(let g=Math.ceil(d0);g<=d1;g+=2)
    s+=`<line x1="$${P}" y1="$${y(g)}" x2="$${W-P}" y2="$${y(g)}" stroke="var(--grid)"/>`+
       `<text x="$${P-4}" y="$${y(g)+3}" fill="var(--muted)" font-size="10" text-anchor="end">$${g} deg</text>`;
  s+=rows.map(d=>{
    const rad=Math.max(2,Math.min(9,(d.r_arcmin||1.5)*1.6));
    const col=d.klass==="SNR"?"var(--snr)":"var(--cand)";
    return `<a href="objects/$${d.slug}.html"><circle cx="$${x(d.ra)}" cy="$${y(d.dec)}" r="$${rad}"
      fill="$${col}" fill-opacity="0.86" stroke="#faf8f3" stroke-width="1.4"><title>$${d.id}</title></circle></a>`;}).join("");
  svg.innerHTML=s;
}
["q","fclass","ftype"].forEach(id=>document.getElementById(id).oninput=render);
document.getElementById("clear").onclick=()=>{
  document.getElementById("q").value="";
  document.getElementById("fclass").value="";
  document.getElementById("ftype").value="";
  render();
};
render();
</script>""").substitute(n_snr=n_snr, n_cand=n_cand, n_total=len(df), data=json.dumps(records))
    return PAGE.substitute(
        title=SITE_NAME, root=".", site_name=SITE_NAME, body=body,
        version=version, version_note=VERSION_NOTE, head_extra="",
    )


def about_page(version: str) -> str:
    body = """
<h1>About this catalog</h1>
<p>This is a living, literature-consolidated census of supernova remnants in
the Large Magellanic Cloud — intended as the LMC counterpart to
<a href="https://www.mrao.cam.ac.uk/surveys/snrs/">Green's Galactic SNR
catalog</a> and <a href="http://snrcat.physics.umanitoba.ca/">SNRcat</a>.</p>
<h3>Classification criteria</h3>
<p>An object is a <strong>confirmed SNR</strong> when it satisfies at least
two of the three classical criteria (Filipović et al. 1998; Bozzetto et al.
2017): (1) non-thermal radio spectral index α &lt; −0.4; (2) diffuse X-ray
emission; (3) shock-enhanced [S II]/Hα ≥ 0.4. One criterion → candidate.</p>
<h3>Sources</h3>
<ul>
<li>Maggi et al. 2016, A&amp;A 585, A162 (XMM-Newton X-ray population)</li>
<li>Bozzetto et al. 2017, ApJS 230, 2 (radio/statistical)</li>
<li>Leahy 2017, ApJ 837, 36 (energetics)</li>
<li>Yew et al. 2021, MNRAS 500, 2336 (optical)</li>
<li>Kavanagh et al. 2022, MNRAS 515, 4099 (XMM faint/evolved)</li>
<li>Bozzetto et al. 2022, MNRAS 518, 2574 (ASKAP)</li>
<li>Zangrandi et al. 2024, A&amp;A 692, A237 (eROSITA census)</li>
<li>Shukla 2024, <a href="https://github.com/whyvav/MThesis">MSc thesis</a> (consolidation; J0500-6512 confirmation)</li>
</ul>
<h3>How to cite</h3>
<p>Until the accompanying paper is published, please cite this website by URL & data version, and the
<a href="https://github.com/whyvav/MThesis">Master's Thesis</a> this catalog builds on:</p>
<pre><code>@mastersthesis{Shukla2024_MThesis,
	title = {X-ray {Evolution} of {Supernova} {Remnants} in the {Large} {Magellanic} {Cloud}},
	shorttitle = {X-ray {Evolution} of {MCSNRs}},
	url = {https://www.sternwarte.uni-erlangen.de/docs/theses/2024-11_Shukla.pdf},
	language = {en},
	school = {FAU},
	author = {Shukla, Vaibhav},
	month = nov,
	year = {2024}
}</code></pre>
<h3>Imagery</h3>
<p>Object pages stream survey imagery client-side via
<a href="https://aladin.cds.unistra.fr/">Aladin Lite</a> (DSS2, SHASSA Hα,
eROSITA-DE DR1, XMM-Newton EPIC, RACS-low, SUMSS, GALEX, AllWISE, 2MASS
HiPS), and — where generated — show pipeline cutout PNGs (eROSITA-DE DR1
X-ray, DeMCELS DR1 Hα &amp; [S II], ASKAP-EMU 888 MHz) built by the
<a href="https://github.com/whyvav/VLMism">VLMism</a> pipeline, with
per-file provenance in <a href="images/manifest.csv">images/manifest.csv</a>.
Credits: eROSITA-DE (Merloni et al. 2024); DeMCELS (Points et al. 2024,
NSF NOIRLab); ASKAP-EMU (Pennock et al. 2021, CSIRO/CASDA); SHASSA
(Gaustad et al. 2001). Cutouts marked "quick-look" come from
<a href="https://alasky.cds.unistra.fr/hips-image-services/hips2fits">CDS
hips2fits</a> and are for visualization only.</p>
<h3>Data &amp; feedback</h3>
<p>Download: <a href="catalog.csv">CSV</a> · <a href="catalog.json">JSON</a> ·
cutout images <a href="images/manifest.csv">manifest</a>.
Corrections and new-object reports: open an issue on the repository.</p>"""
    return PAGE.substitute(
        title=f"About — {SITE_NAME}", root=".", site_name=SITE_NAME, body=body,
        version=version, version_note=VERSION_NOTE, head_extra="",
    )


STYLE = """
:root {
  --paper:#faf8f3;
  --sand:#efebe1;
  --surface:#fffdfa;
  --panel:#ffffff;
  --ink:#211f1a;
  --muted:#5f594d;
  --faint:#8a8474;
  --line:#e2dccf;
  --line-strong:#cfc6b6;
  --snr:#35619c;
  --snr-soft:#e7f0fb;
  --cand:#b7763a;
  --cand-soft:#f5eadf;
  --wood:#9c6b43;
  --cyan:#1596a7;
  --grid:#d8d1c4;
  --shadow:0 18px 45px rgba(33,31,26,.08);
}
* { box-sizing:border-box; }
sup, sub { font-size:75%; line-height:0; position:relative; vertical-align:baseline; }
sup { top:-0.5em; }
sub { bottom:-0.25em; }
html { color-scheme:light; }
body {
  margin:0;
  background:var(--paper);
  color:var(--ink);
  font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  line-height:1.5;
  overflow-x:hidden;
}
a { color:var(--snr); text-decoration:none; }
a:hover { color:#294c7d; text-decoration:underline; text-underline-offset:3px; }
.site-header {
  display:grid;
  grid-template-columns:minmax(260px,1fr) auto auto;
  gap:22px;
  align-items:center;
  padding:14px clamp(18px,4vw,56px);
  background:rgba(250,248,243,.94);
  border-bottom:1px solid var(--line);
  position:sticky;
  top:0;
  z-index:20;
  backdrop-filter:blur(12px);
}
.brand-lockup { display:flex; align-items:center; gap:14px; color:var(--ink); min-width:0; }
.brand-lockup:hover { text-decoration:none; color:var(--ink); }
.brand-mark { width:78px; height:54px; object-fit:contain; flex:none; }
.brand-copy { display:flex; flex-direction:column; min-width:0; }
.brand-word { font-size:clamp(25px,3vw,38px); font-weight:650; letter-spacing:0; line-height:1; white-space:nowrap; }
.brand-tagline { margin-top:5px; color:var(--muted); font:500 12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; letter-spacing:.04em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.site-nav { display:flex; align-items:center; gap:4px; }
.site-nav a { color:var(--ink); padding:8px 12px; border-radius:6px; font-weight:600; font-size:14px; }
.site-nav a:hover { background:var(--sand); text-decoration:none; }
.ver { justify-self:end; border:1px solid var(--line); border-radius:6px; padding:7px 10px; color:var(--muted); font-size:12px; background:var(--surface); white-space:nowrap; }
main { width:min(calc(100% - 40px), 1440px); margin:0 auto; padding:34px 0 24px; }
.site-footer { width:min(calc(100% - 40px), 1440px); margin:0 auto; padding:24px 0 34px; color:var(--muted); font-size:12px; border-top:1px solid var(--line); display:flex; flex-wrap:wrap; gap:8px 18px; }
h1 { margin:0; font-size:clamp(32px,4.4vw,58px); line-height:1.05; letter-spacing:0; font-weight:720; }
/* Homepage only */
.hero h1 {
  font-size:clamp(28px,3vw,40px);
  line-height:1.12;
}
h2 { margin:0; font-size:16px; line-height:1.2; }
h3 { margin:0 0 8px; color:var(--ink); font-size:15px; }
.lede { max-width:78ch; margin:12px 0 0; color:#3f3a33; font-size:16px; }
.hero { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:28px; align-items:end; margin-bottom:24px; }
.stat-grid { display:grid; grid-template-columns:repeat(3,minmax(118px,1fr)); gap:10px; }
.stat-card { display:grid; gap:2px; border:1px solid var(--line); border-radius:8px; padding:14px 16px; background:var(--surface); min-width:118px; }
.stat-card strong { color:var(--snr); font-size:28px; line-height:1; }
.stat-card span { color:var(--muted); font-size:12px; font-weight:650; }
.stat-card.cand strong { color:var(--cand); }
.stat-card.total strong { color:var(--ink); }
.workbench, .viewer-panel, .property-stack section, .cutouts, pre {
  background:var(--panel);
  border:1px solid var(--line);
  border-radius:8px;
  box-shadow:var(--shadow);
}
.workbench { overflow:hidden; min-width:0; }
#controls { display:flex; align-items:end; gap:12px; flex-wrap:wrap; padding:14px; border-bottom:1px solid var(--line); }
#controls label { display:grid; gap:5px; color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.08em; }
.search-field { flex:1 1 300px; }
input, select, button {
  height:40px;
  background:var(--surface);
  color:var(--ink);
  border:1px solid var(--line-strong);
  border-radius:6px;
  padding:0 11px;
  font:600 14px ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
}
input { width:100%; min-width:0; font-weight:500; }
button { cursor:pointer; }
button:hover, input:focus, select:focus { border-color:var(--snr); outline:none; }
.count { margin-left:auto; color:var(--ink); font-weight:750; padding:9px 0; white-space:nowrap; }
#wrap { display:grid; grid-template-columns:minmax(340px, 470px) minmax(0,1fr); min-height:560px; min-width:0; }
#skybox { border-right:1px solid var(--line); padding:0; background:linear-gradient(180deg,#fffdfa 0%,#faf8f3 100%); min-width:0; }
.panel-head { padding:13px 16px; border-bottom:1px solid var(--line); }
.panel-head h2 span { color:var(--faint); font:700 11px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; margin-left:6px; }
#sky { display:block; width:100%; height:auto; max-height:430px; }
.map-legend { display:flex; gap:16px; align-items:center; padding:8px 16px 0; color:var(--muted); font-size:12px; }
.dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:-1px; }
.dot.snr { background:var(--snr); }
.dot.cand { background:var(--cand); }
.note { color:var(--muted); font-size:11.5px; opacity:.9; }
#skybox .note { margin:7px 16px 14px; }
#tablebox { min-width:0; max-width:100%; overflow:auto; background:var(--panel); }
table { border-collapse:collapse; width:100%; font-size:12.5px; }
#tbl { min-width:1040px; }
th,td { padding:9px 12px; text-align:left; white-space:nowrap; border-bottom:1px solid #eee8dc; }
th { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
#tbl th { position:sticky; top:0; z-index:1; background:#fbf8f1; cursor:pointer; user-select:none; }
#tbl tr:hover { background:#f6f0e7; }
.status-pill, .type-pill {
  display:inline-flex;
  align-items:center;
  min-height:22px;
  border-radius:6px;
  padding:2px 8px;
  font-size:12px;
  font-weight:750;
  line-height:1;
}
.status-pill.snr { color:var(--snr); background:var(--snr-soft); border:1px solid #b8d0ee; }
.status-pill.cand { color:#9b4f16; background:var(--cand-soft); border:1px solid #e2c4a7; }
.type-pill { color:#276071; background:#e1f4f6; border:1px solid #b9dfe5; }
.coverage { display:grid; grid-template-columns:1.2fr repeat(4,1fr); gap:18px; align-items:start; margin-top:24px; padding:22px 0 0; border-top:1px solid var(--line); }
.coverage h2 { font-size:22px; }
.coverage div { display:grid; gap:3px; border-left:1px solid var(--line); padding-left:16px; }
.coverage strong { color:var(--ink); }
.coverage span { color:var(--muted); font-size:12px; }
.page-head { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; margin-bottom:20px; }
.object-head h1 { font-size:clamp(30px,4vw,48px); }
.banner { background:#f6eadf; border-left:3px solid var(--cand); padding:10px 12px; font-size:13px; border-radius:6px; }
.objgrid { display:grid; grid-template-columns:minmax(360px,1.05fr) minmax(360px,.95fr); gap:18px; }
.viewer-panel { padding:12px; }
#aladin { border-radius:6px; overflow:hidden; background:#111; }
.controls { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:10px 0; }
.linkrow { display:flex; gap:10px; margin:10px 0 2px; flex-wrap:wrap; }
.linkrow a { border:1px solid var(--line); border-radius:6px; padding:6px 9px; background:var(--surface); font-size:13px; font-weight:650; }
.property-stack { display:grid; gap:10px; }
.property-stack section { padding:12px; box-shadow:none; }
.property-stack table th { width:56%; color:var(--muted); font-weight:650; text-transform:none; letter-spacing:0; font-size:12px; }
.property-stack table th .note { margin-left:6px; }
pre { padding:14px; overflow-x:auto; font-size:12px; box-shadow:none; }
ul { padding-left:22px; }
.cutouts { padding:14px; margin-top:18px; }
.cutgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(170px,1fr)); gap:12px; }
.cutgrid figure { margin:0; }
.cutgrid img { width:100%; border-radius:6px; display:block; image-rendering:auto; border:1px solid var(--line); }
.cutgrid figcaption { font-size:11.5px; margin-top:5px; line-height:1.35; color:var(--muted); }
.cutgrid figcaption .note { display:block; margin-left:0; }
.viz { background:var(--cand); color:#fff; border-radius:6px; padding:1px 6px; font-size:10px; margin-left:6px; }
@media (max-width:1100px) {
  .site-header { grid-template-columns:1fr; gap:10px; position:static; }
  .site-nav { order:2; overflow-x:auto; }
  .ver { justify-self:start; }
  .hero { grid-template-columns:1fr; }
  .stat-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
  #wrap, .objgrid, .coverage { grid-template-columns:1fr; }
  #skybox { border-right:0; border-bottom:1px solid var(--line); }
}
@media (max-width:680px) {
  main, .site-footer { width:min(calc(100% - 28px), 1440px); }
  h1 { font-size:28px; line-height:1.08; }
  .hero > div:first-child, .hero h1, .hero .lede { max-width:22.5rem; }
  .brand-mark { width:58px; height:40px; }
  .brand-word { font-size:25px; }
  .brand-tagline { display:none; }
  .stat-grid { grid-template-columns:1fr; }
  #controls { align-items:stretch; }
  #controls label, #controls button, .count { width:100%; }
  .count { margin-left:0; padding:0; }
  th,td { padding:8px 10px; }
}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog", default=None,
        help="path to catalog CSV (default: highest-versioned data/lmc_snrs_extended_v*.csv)",
    )
    parser.add_argument("--out", default="site")
    parser.add_argument(
        "--images", default="images",
        help="directory of pipeline-generated cutout PNGs (default: images/; "
        "skipped silently when absent)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    catalog = Path(args.catalog) if args.catalog else latest_catalog()
    logger.info("building from %s", catalog)
    df = pd.read_csv(catalog)
    validate_catalog(df)
    args.catalog = str(catalog)
    version = Path(args.catalog).stem.replace("lmc_snrs_extended_", "")
    out = Path(args.out)
    (out / "objects").mkdir(parents=True, exist_ok=True)
    (out / "brand").mkdir(parents=True, exist_ok=True)

    (out / "style.css").write_text(STYLE, encoding="utf-8")
    for asset in ("logo-mark.svg", "logo-lockup.svg", "favicon.svg"):
        shutil.copy(Path("brand") / asset, out / "brand" / asset)
    (out / "index.html").write_text(index_page(df, version), encoding="utf-8")
    (out / "about.html").write_text(about_page(version), encoding="utf-8")
    df.to_csv(out / "catalog.csv", index=False)
    (out / "catalog.json").write_text(
        df.replace({np.nan: None}).to_json(orient="records"), encoding="utf-8"
    )
    images_dir = Path(args.images)
    image_index = load_image_manifest(images_dir)
    if image_index:
        shutil.copytree(images_dir, out / "images", dirs_exist_ok=True)

    for _, row in df.iterrows():
        slug = slugify(row["id"])
        page = object_page(row, version, obj_images=image_index.get(slug))
        (out / "objects" / f"{slug}.html").write_text(page, encoding="utf-8")
    shutil.copy(args.catalog, out / Path(args.catalog).name)
    logger.info(
        "built %s: %d object pages (%d with cutout PNGs)",
        out, len(df), sum(1 for s in image_index if any(image_index[s])),
    )


if __name__ == "__main__":
    main()
