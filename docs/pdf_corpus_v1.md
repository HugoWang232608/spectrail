# M5.3 Real-World PDF Corpus v1

`pdf_corpus_v1` is a parser-level acceptance suite. It evaluates
`PdfParserV2`, `ParsedDocument`, and the finalized `EvidenceIndex` directly;
it does not invoke a model or compare generated requirement statements.

This separation keeps two questions distinct:

```text
PDF corpus:
  Did the parser preserve and classify trustworthy source Evidence?

Extraction evaluation:
  Did the model and validators produce the expected grounded ReqIR?
```

## Running the core corpus

```bash
python -m spectrail evaluate-pdf-corpus \
  eval/pdf_corpus_v1/manifest.json \
  --output outputs/pdf-corpus
```

Add `--include-extended` to include cases whose `tier` is `extended`.
The command writes:

```text
outputs/pdf-corpus/.spectrail-pdf-corpus-output
outputs/pdf-corpus/pdf_corpus_report.json
outputs/pdf-corpus/pdf_corpus_report.md
```

An existing non-empty output directory must contain the ownership marker.
After manifest validation, the runner removes its known final and staging paths
before parsing any case, so an interrupted run cannot leave an earlier
successful report looking current. A managed file or symlink is unlinked
without following the link; a managed directory fails closed. Other files are
left untouched, and the runner never recursively deletes the output root.

Successful publication stages and validates both reports, fsyncs their content,
then publishes Markdown followed by JSON. The JSON report is the authoritative
machine result and is published last; Markdown is a rebuildable projection. If
staging fails, neither final report is published.

## Manifest contract

Each case records:

```text
case_id
document
tier = core | extended
source provenance, normalized producer_family_id, display producer family,
URL and redistribution status
source_sha256
expected PDF title, creator or producer metadata for every core case
expected parser name/version
optional default and platform-specific Evidence fingerprints
typed observations
```

Paths are resolved relative to the manifest. Source SHA-256, parser identity,
declared PDF metadata, and an optional Evidence fingerprint are checked before
a case can pass. External documents must declare a source URL. Core cases must
lock PDF metadata and cannot be download-only. Producer counts use the stable,
normalized `producer_family_id`, while `producer_family` remains a display
label. These rules prevent producer provenance from being only an unchecked
label.

Exact Evidence fingerprints default to `expected_evidence_fingerprint`. A case
may override it in `expected_evidence_fingerprints_by_platform`, keyed by the
reported runtime identity such as `linux-x86_64` or `darwin-arm64`. This is only
for stable native-library geometry differences: observations and capability
gates remain identical on every platform. Fingerprint changes report an
intentional stale fixture instead of silently accepting new parser output.

The observation types are:

```text
text_source
  selected quote, page, block type, section path and capabilities

heading_page
  exhaustive normalized heading set for one page

table_page
  exhaustive ordered TableRecord topology for one page

fallback_block
  text retained from an untrusted table candidate, with table_cell expected
  but unavailable

continuation_pair
  an explicitly linked or explicitly independent pair of page-local tables
```

Every observation declares `gate=true` by default. Only `heading_page` may set
`gate=false`; all text, table, fallback, and continuation observations always
participate in the release gate. A report-only heading still contributes
diagnostic heading metrics but cannot fail its case. This keeps release metrics
from being silently weakened while heading inference accumulates enough real
producer annotations for stable precision/recall thresholds.

## Metrics and zero denominators

The suite reports:

```text
case pass rate
case count, producer-family count, external-document count,
redistribution-reviewed count and metadata-locked case count
gated observation pass rate and evaluated count
selected text-source accuracy and evaluated count
page-region availability rate and evaluated count
selected table-topology precision / recall and evaluated count
fallback accuracy and evaluated count
continuation-pair accuracy and false-positive count
heading precision / recall and evaluated count
```

A metric with no evaluated observations is `null`, not a synthetic perfect
score. Any threshold applied to a `null` metric fails. Count thresholds should
accompany rate thresholds as the corpus grows so an empty category cannot pass
the release gate. Threshold names are checked against the report's metric
allowlist; a typo fails manifest loading with
`PDF_CORPUS_THRESHOLD_UNKNOWN_METRIC` instead of appearing as an ordinary
quality failure with a `null` value.

## Provenance tiers

The corpus has two layers:

- `core`: 4–6 small, checked-in PDFs, with a separately gated reviewed-
  redistribution count;
- `extended`: 8–12 provenance-locked PDFs that may be download-only or
  customer-controlled and run on demand.

The checked core now contains five cases across four actual producer families:

- the external IEEE 29148 example produced by Microsoft Word for Office 365;
- the external `booktabs` package manual produced by pdfTeX;
- the locked LibreOffice merged-table corpus fixture;
- deterministic ReportLab simple-table and authored-continuation fixtures.

The `booktabs` PDF is the unmodified CTAN package documentation under LPPL
1.3c; its download URL, package version, hash, and PDF metadata are recorded in
`tests/fixtures/pdf_corpus_booktabs.provenance.json`. The IEEE source URL and
checked bytes are recorded, but its redistribution terms have not yet been
independently classified; the manifest says so explicitly rather than claiming
a license. Project-authored fixtures are labelled `project_fixture`, not
presented as external documents.

New checked-in documents must record their source URL or origin, producer
family, SHA-256, PDF metadata expectation, redistribution decision, and license
note. Release CI never downloads mutable URLs. Download-only sources belong in
the extended tier and must be materialized by a separate provenance- and
hash-checking workflow.

## Trust policy

The corpus follows the same Evidence rule as the parser:

> Abstention is acceptable; plausible but unsupported structured Evidence is
> not.

Accepted table topology and continuation relations gate at precision `1.0`,
with non-zero evaluated counts. The LibreOffice case is also a continuation
false-positive control: two adjacent 2x2 tables without authored markers remain
independent.
Unsupported or ambiguous structures must remain readable text while
`table_cell` is expected but unavailable. Heading precision and recall remain
separate report-only metrics until real corpus failures drive the next parser
change.
