# images/ — pipeline-generated multiwavelength cutouts

One folder per catalog object, written by the VLMism Phase-2 pipeline
(`VLMism/scripts/04_build_snr_images.py`) — **do not edit these by hand**;
regenerate them instead.

**Gitignored while private/unpublished.** VLMism and this catalog are an
work-in-progress project, so `images/*/` and `images/manifest.csv` are 
gitignored — only this README is tracked in the public repo. 
`build.py` still picks the directory up automatically for
local builds/previews (see the repo README's "Imagery" section); the
public GitHub Pages site simply omits the cutout panels and links until
this is revisited.

```
images/
├── manifest.csv                      provenance: one row per PNG
│                                     (survey, PSF, viz_grade flag, catalog
│                                     version, UTC timestamp)
└── <slug>/                           e.g. MCSNR_J0448-6700/
    ├── <slug>_rgb.png                composite: R radio / G Hα / B X-ray
    ├── <slug>_xray_soft.png          eROSITA-DE DR1, 0.2–2.3 keV rate
    ├── <slug>_halpha.png             DeMCELS DR1 N662
    ├── <slug>_sii.png                DeMCELS DR1 N673
    ├── <slug>_sii_halpha_ratio.png   derived after PSF matching
    └── <slug>_radio_888.png          ASKAP-EMU ES 888 MHz (Pennock+21)
```

All PNGs are asinh-stretched (1–99.5 percentile clip), 256×256, north up.
Rows with `viz_grade=True` in the manifest were fetched via CDS hips2fits
(e.g. SHASSA Hα where DeMCELS DR1 has no coverage) — **visualization only,
never for photometry**. Bands can be missing where no public survey covers
the object (noted in the manifest gaps).

`build.py` picks this directory up automatically (`--images images`) and
copies it into the built site; object pages show whatever bands exist.

Survey credits and access details: `VLMism/docs/DATA_SOURCING.md`.
Licensing of these derived images follows the catalog's CC-BY 4.0 with the
underlying-survey acknowledgments listed on the site's About page.
