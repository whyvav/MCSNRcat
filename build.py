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
import shutil
from pathlib import Path
from string import Template

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SITE_NAME = "MCSNRcat"
VERSION_NOTE = "Maintained by V. Shukla; assembled from the literature (see About)."

#: HiPS surveys offered in the per-object viewer. IDs must exist on the CDS
#: HiPS network — verify at https://aladin.cds.unistra.fr/hips/list before
#: adding more (e.g. eROSITA, SUMSS, GLEAM when available).
ALADIN_SURVEYS = [
    ("CDS/P/DSS2/color", "DSS2 optical"),
    ("CDS/P/GALEXGR6/AIS/color", "GALEX UV"),
    ("CDS/P/allWISE/color", "AllWISE mid-IR"),
    ("CDS/P/2MASS/color", "2MASS near-IR"),
]

PROPERTY_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Identification", [
        ("id", "Catalog ID", ""),
        ("alias", "Alias / common name", ""),
        ("klass", "Status", ""),
        ("sn_type", "SN type", "Ia = thermonuclear, CC = core collapse; '?' = tentative"),
        ("ref_discovery", "Discovery / classification ref", ""),
    ]),
    ("Position (ICRS)", [
        ("ra", "RA [deg]", ""),
        ("dec", "Dec [deg]", ""),
    ]),
    ("Morphology", [
        ("size_maj_arcmin", "Major axis [arcmin]", ""),
        ("size_min_arcmin", "Minor axis [arcmin]", ""),
        ("d_arcmin", "Mean diameter [arcmin]", ""),
        ("d_pc", "Diameter [pc]", "at 50 kpc"),
        ("pa_deg", "Position angle [deg]", ""),
        ("shape", "Fitted shape", ""),
        ("ovality", "Ovality", "Zangrandi+24 convention"),
        ("eccentricity", "Eccentricity", ""),
    ]),
    ("X-ray (eROSITA / XMM)", [
        ("xray_rate_ctss", "eRASS rate [cts/s]", "upper limit if flagged"),
        ("xray_rate_err", "Rate error", ""),
        ("lx_1e35", "L_X [10^35 erg/s]", "Maggi+16, 0.3-8 keV"),
        ("nh_1e21", "N_H [10^21 cm^-2]", "Maggi+16"),
        ("age_kyr", "Age [kyr]", "Maggi+16"),
    ]),
    ("Radio (Bozzetto+17)", [
        ("alpha_radio", "Spectral index α", "S_ν ∝ ν^α; α < -0.4 non-thermal"),
        ("alpha_radio_err", "α error", ""),
        ("s_1ghz_jy", "S_1GHz [Jy]", ""),
    ]),
    ("Energetics (Leahy 17)", [
        ("e0_1e51_erg", "E0 [10^51 erg]", ""),
        ("age_l17_yr", "Age [yr]", ""),
        ("n0_cm3", "n0 [cm^-3]", ""),
    ]),
]

PAGE = Template("""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<link rel="stylesheet" href="$root/style.css">
$head_extra
</head><body>
<nav><a href="$root/index.html"><strong>$site_name</strong></a>
<a href="$root/index.html">Census</a>
<a href="$root/about.html">About</a>
<a href="$root/catalog.csv">CSV</a>
<a href="$root/catalog.json">JSON</a>
<span class="ver">$version</span></nav>
<main>$body</main>
<footer>$version_note Data version: <code>$version</code>.
Imagery: CDS Aladin Lite / HiPS. Built from the
<a href="https://github.com/whyvav/MCSNRcat">source on GitHub</a>.</footer>
</body></html>""")


def slugify(obj_id: str) -> str:
    return obj_id.replace(" ", "_").replace("/", "-")


def fmt(v: object, nd: int = 3) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{nd}g}"
    return str(v)


def object_page(row: pd.Series, version: str) -> str:
    fov = max(3.0 * (row.get("d_arcmin") or 4.0) / 60.0, 0.12)
    surveys_js = json.dumps([s for s, _ in ALADIN_SURVEYS])
    options = "".join(
        f'<option value="{sid}">{label}</option>' for sid, label in ALADIN_SURVEYS
    )
    groups_html = ""
    for gname, fields in PROPERTY_GROUPS:
        rows_html = ""
        for key, label, note in fields:
            val = fmt(row.get(key))
            if key == "xray_rate_ctss" and row.get("xray_rate_is_upper_limit"):
                val = f"&lt; {val}"
            note_html = f'<span class="note">{note}</span>' if note else ""
            rows_html += f"<tr><th>{label}{note_html}</th><td>{val}</td></tr>"
        groups_html += f"<section><h3>{gname}</h3><table>{rows_html}</table></section>"

    thesis_note = row.get("thesis_note")
    banner = (
        f'<p class="banner">{thesis_note}</p>' if isinstance(thesis_note, str) else ""
    )
    ra, dec = row["ra"], row["dec"]
    body = f"""
<h1>{row['id']} <span class="pill {'snr' if row['klass'] == 'SNR' else 'cand'}">{row['klass'].replace('_', ' ')}</span></h1>
{banner}
<div class="objgrid">
  <div>
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
  </div>
  <div>{groups_html}</div>
</div>
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
<h1>Supernova remnants in the Large Magellanic Cloud</h1>
<p class="lede">A living census of all known LMC SNRs —
<span class="pill snr">$n_snr confirmed</span>
<span class="pill cand">$n_cand candidates</span> —
consolidated from X-ray, radio, and optical literature
(Maggi+16, Bozzetto+17/22, Yew+21, Kavanagh+22, Zangrandi+24, Shukla 24).
Click any object for a multiwavelength viewer and full properties.</p>
<div id="controls">
  <input id="q" placeholder="search id / alias / ref…" size="28">
  <select id="fclass"><option value="">status: all</option><option value="SNR">confirmed</option><option value="SNR_candidate">candidate</option></select>
  <select id="ftype"><option value="">type: all</option><option>Ia</option><option>Ia?</option><option>CC</option><option>CC?</option></select>
  <span class="note" id="count"></span>
</div>
<div id="wrap">
  <div id="skybox"><svg id="sky" width="460" height="430"></svg>
    <div class="note">RA increases leftward. Marker size ∝ angular radius. Click a marker to open the object page.</div></div>
  <div id="tablebox"><table id="tbl"><thead></thead><tbody></tbody></table></div>
</div>
<script>
const DATA = $data;
const COLS = [
 {key:"id",label:"ID"},{key:"klass",label:"Status"},{key:"sn_type",label:"Type"},
 {key:"ra",label:"RA°"},{key:"dec",label:"Dec°"},{key:"r_arcmin",label:"r (')"},
 {key:"d_pc",label:"D (pc)"},{key:"alpha_radio",label:"α radio"},
 {key:"age_kyr",label:"Age (kyr)"},{key:"alias",label:"Alias"},{key:"ref_discovery_code",label:"Ref"}];
let sortKey="ra", sortAsc=true;
const fmt=(v,k)=>v==null?"":(typeof v==="number"&&!["ra","dec"].includes(k)?+v.toFixed(2):(typeof v==="number"?+v.toFixed(4):v));
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
  document.querySelector("#tbl tbody").innerHTML=rows.map(d=>"<tr>"+COLS.map(c=>
    c.key==="id"?`<td><a href="objects/$${d.slug}.html">$${d.id}</a></td>`
    :`<td>$${fmt(d[c.key],c.key)}</td>`).join("")+"</tr>").join("");
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
    s+=`<line x1="$${x(g)}" y1="$${P}" x2="$${x(g)}" y2="$${H-P}" stroke="#334155"/>`+
       `<text x="$${x(g)}" y="$${H-P+14}" fill="#94a3b8" font-size="9" text-anchor="middle">$${g}°</text>`;
  for(let g=Math.ceil(d0);g<=d1;g+=2)
    s+=`<line x1="$${P}" y1="$${y(g)}" x2="$${W-P}" y2="$${y(g)}" stroke="#334155"/>`+
       `<text x="$${P-4}" y="$${y(g)+3}" fill="#94a3b8" font-size="9" text-anchor="end">$${g}°</text>`;
  s+=rows.map(d=>{
    const rad=Math.max(2,Math.min(9,(d.r_arcmin||1.5)*1.6));
    const col=d.klass==="SNR"?"var(--snr)":"var(--cand)";
    return `<a href="objects/$${d.slug}.html"><circle cx="$${x(d.ra)}" cy="$${y(d.dec)}" r="$${rad}"
      fill="$${col}" fill-opacity="0.65" stroke="$${col}"><title>$${d.id}</title></circle></a>`;}).join("");
  svg.innerHTML=s;
}
["q","fclass","ftype"].forEach(id=>document.getElementById(id).oninput=render);
render();
</script>""").substitute(n_snr=n_snr, n_cand=n_cand, data=json.dumps(records))
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
<h3>Data &amp; feedback</h3>
<p>Download: <a href="catalog.csv">CSV</a> · <a href="catalog.json">JSON</a>.
Corrections and new-object reports: open an issue on the repository.</p>"""
    return PAGE.substitute(
        title=f"About — {SITE_NAME}", root=".", site_name=SITE_NAME, body=body,
        version=version, version_note=VERSION_NOTE, head_extra="",
    )


STYLE = """
:root { --snr:#2563eb; --cand:#ea580c; --bg:#0f172a; --panel:#1e293b; --tx:#e2e8f0; }
* { box-sizing:border-box; }
body { font-family:system-ui,sans-serif; margin:0; background:var(--bg); color:var(--tx); }
nav { display:flex; gap:18px; align-items:baseline; padding:12px 24px; background:var(--panel); flex-wrap:wrap; }
nav a { color:var(--tx); text-decoration:none; } nav a:hover { color:#7dd3fc; }
nav .ver { margin-left:auto; font-size:11px; opacity:.6; }
main { padding:18px 24px; max-width:1280px; margin:0 auto; }
footer { padding:14px 24px; font-size:11.5px; opacity:.65; }
h1 { font-size:22px; } h3 { margin:14px 0 6px; color:#93c5fd; }
.lede { max-width:70ch; }
.pill { padding:2px 10px; border-radius:12px; font-size:12px; color:#fff; }
.snr { background:var(--snr); } .cand { background:var(--cand); }
.note { font-size:11px; opacity:.7; margin-left:6px; }
.banner { background:#374151; border-left:3px solid #7dd3fc; padding:8px 12px; font-size:13px; }
#controls { display:flex; gap:12px; flex-wrap:wrap; margin:12px 0; }
input,select { background:var(--panel); color:var(--tx); border:1px solid #334155; border-radius:6px; padding:6px 10px; }
#wrap { display:flex; gap:14px; flex-wrap:wrap; }
#skybox { background:var(--panel); border-radius:10px; padding:10px; }
#tablebox { flex:1; min-width:600px; max-height:76vh; overflow:auto; background:var(--panel); border-radius:10px; }
table { border-collapse:collapse; width:100%; font-size:12.5px; }
th,td { padding:5px 8px; text-align:left; white-space:nowrap; }
#tbl th { position:sticky; top:0; background:#334155; cursor:pointer; user-select:none; }
tr:nth-child(even) { background:#24334a; } #tbl tr:hover { background:#3b4d68; }
a { color:#7dd3fc; }
.objgrid { display:grid; grid-template-columns: minmax(340px,1fr) minmax(340px,1fr); gap:18px; }
@media (max-width:900px){ .objgrid { grid-template-columns:1fr; } }
.objgrid section { background:var(--panel); border-radius:10px; padding:8px 12px; margin-bottom:10px; }
.objgrid table th { width:56%; font-weight:500; opacity:.85; }
.controls { margin:8px 0; } .linkrow { display:flex; gap:14px; margin:8px 0; flex-wrap:wrap; }
pre { background:var(--panel); border-radius:8px; padding:10px 14px; overflow-x:auto; font-size:12px; }
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="data/lmc_snrs_extended_v2.csv")
    parser.add_argument("--out", default="site")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    df = pd.read_csv(args.catalog)
    version = Path(args.catalog).stem.replace("lmc_snrs_extended_", "")
    out = Path(args.out)
    (out / "objects").mkdir(parents=True, exist_ok=True)

    (out / "style.css").write_text(STYLE, encoding="utf-8")
    (out / "index.html").write_text(index_page(df, version), encoding="utf-8")
    (out / "about.html").write_text(about_page(version), encoding="utf-8")
    df.to_csv(out / "catalog.csv", index=False)
    (out / "catalog.json").write_text(
        df.replace({np.nan: None}).to_json(orient="records"), encoding="utf-8"
    )
    for _, row in df.iterrows():
        page = object_page(row, version)
        (out / "objects" / f"{slugify(row['id'])}.html").write_text(page, encoding="utf-8")
    shutil.copy(args.catalog, out / Path(args.catalog).name)
    logger.info("built %s: %d object pages (+index/about/downloads)", out, len(df))


if __name__ == "__main__":
    main()
