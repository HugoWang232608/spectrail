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
The runner only replaces its two known report files and never recursively
deletes the output root.

## Manifest contract

Each case records:

```text
case_id
document
tier = core | extended
source provenance, producer family, URL and redistribution status
source_sha256
expected parser name/version
optional Evidence fingerprint
typed observations
```

Paths are resolved relative to the manifest. Source SHA-256, parser identity,
and an optional Evidence fingerprint are checked before a case can pass.
Fingerprint changes therefore report an intentional stale fixture instead of
silently accepting new parser output.

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

Every observation declares `gate=true` by default. A report-only observation
still contributes diagnostic metrics but cannot fail its case. This is used for
heading inference until enough real producers are annotated to set stable
precision/recall thresholds.

## Metrics and zero denominators

The suite reports:

```text
case pass rate
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
the release gate.

## Provenance tiers

The target corpus has two layers:

- `core`: 4–6 small, checked-in PDFs with reviewed redistribution status;
- `extended`: 8–12 provenance-locked PDFs that may be download-only or
  customer-controlled and run on demand.

The initial seed is the existing external IEEE 29148 example produced by
Microsoft Word for Office 365. Its origin URL and checked bytes are recorded,
but its redistribution terms have not yet been independently classified; the
manifest says so explicitly rather than claiming a license.

New checked-in documents must record their source URL or origin, producer
family, SHA-256, redistribution decision, and license note. Release CI must not
download mutable URLs. Download-only sources belong in the extended tier and
must be materialized by a separate provenance- and hash-checking workflow.

## Trust policy

The corpus follows the same Evidence rule as the parser:

> Abstention is acceptable; plausible but unsupported structured Evidence is
> not.

Accepted table topology and continuation relations should eventually gate at
precision `1.0`, with negative examples and non-zero evaluated counts.
Unsupported or ambiguous structures must remain readable text while
`table_cell` is expected but unavailable. Heading precision and recall remain
separate report-only metrics until real corpus failures drive the next parser
change.
