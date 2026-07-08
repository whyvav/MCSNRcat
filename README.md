# MCSNRcat

A living, multiwavelength census of supernova remnants in the Magellanic Clouds — the MC counterpart to
[Green's Galactic SNR catalogue](https://www.mrao.cam.ac.uk/surveys/snrs/)
and [SNRcat](http://snrcat.physics.umanitoba.ca/). Currently compiles **78 confirmed +
46 candidate** SNRs in LMC from literature. SMC SNRs to be cataloged later.

**Live site:** https://whyvav.github.io/MCSNRcat/

Each object page includes a multiwavelength viewer (DSS2 optical, GALEX UV,
AllWISE mid-IR, 2MASS near-IR via [Aladin Lite](https://aladin.cds.unistra.fr/)),
X-ray/radio/energetics properties, and one-click links out to SIMBAD, ESASky,
ADS, and VizieR.

## Classification criteria

An object is a **confirmed SNR** when it satisfies at least two of the three
classical criteria (Filipović et al. 1998; Bozzetto et al. 2017): (1)
non-thermal radio spectral index α < −0.4; (2) diffuse X-ray emission; (3)
shock-enhanced [S II]/Hα ≥ 0.4. One criterion → candidate.

## Sources

- Maggi et al. 2016, A&A 585, A162 (XMM-Newton X-ray population)
- Bozzetto et al. 2017, ApJS 230, 2 (radio/statistical)
- Leahy 2017, ApJ 837, 36 (energetics)
- Yew et al. 2021, MNRAS 500, 2336 (optical)
- Kavanagh et al. 2022, MNRAS 515, 4099 (XMM faint/evolved)
- Bozzetto et al. 2022, MNRAS 518, 2574 (ASKAP)
- Zangrandi et al. 2024, A&A 692, A237 (eROSITA census)
- Shukla 2024, [MSc thesis](https://github.com/whyvav/MThesis) (consolidation; J0500-6512 confirmation)

## Architecture

**Versioned CSV → `build.py` → static site.** No database, no server code.

```
MCSNRcat/
├── data/lmc_snrs_extended_v2.csv   ← input catalog
├── build.py                        ← generator (pandas + stdlib only)
├── site/                           ← built output (gitignored; CI rebuilds it)
│   ├── index.html                  census table + filters + clickable sky map
│   ├── objects/<ID>.html           ×124: Aladin Lite viewer, grouped
│   │                               properties, SIMBAD/ESASky/ADS/VizieR links
│   ├── about.html                  criteria, sources, citation
│   └── catalog.{csv,json}          machine-readable downloads
└── .github/workflows/deploy.yml    GitHub Pages CI
```

Static by design: free, permanent, versionable, zero maintenance, trivially
mirrored/archived. Multiwavelength imagery is streamed client-side from CDS
HiPS (Aladin Lite), so the repo stores **no images**.

## Run locally

```bash
python build.py --catalog data/lmc_snrs_extended_v2.csv --out site
python -m http.server -d site 8000     # open http://localhost:8000
```

(Opening `site/index.html` directly via a `file://` URL will render the
census table but not the sky viewers — browsers block ES module imports and
some fetches on `file://` origins. Always preview through a local server.)

## Data & feedback

Download: [CSV](https://whyvav.github.io/MCSNRcat/catalog.csv) ·
[JSON](https://whyvav.github.io/MCSNRcat/catalog.json). Corrections, new
objects, or missing references: please
[open an issue](https://github.com/whyvav/MCSNRcat/issues).

## How to cite

Until the accompanying paper is published, please cite this website by URL & data version, and the [Master's Thesis](https://github.com/whyvav/MThesis) this catalog builds on:

```bibtex
@mastersthesis{Shukla2024_MThesis,
	title = {X-ray {Evolution} of {Supernova} {Remnants} in the {Large} {Magellanic} {Cloud}},
	shorttitle = {X-ray {Evolution} of {MCSNRs}},
	url = {https://www.sternwarte.uni-erlangen.de/docs/theses/2024-11_Shukla.pdf},
	language = {en},
	school = {FAU},
	author = {Shukla, Vaibhav},
	month = nov,
	year = {2024}
}
```

## License

Data and content are released under
[CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/) — reuse is welcome
with attribution. See [LICENSE](LICENSE).

---
Maintained by V. Shukla.
