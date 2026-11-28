# HardwareAI Knowledge Base

This directory contains versioned, structured metadata used by HardwareAI.
It is separate from application source code. Standards documents are not
mirrored here: only source metadata, reviewed aliases, schemas, and taxonomies
are tracked until collection and redistribution permissions are confirmed.

## Structure

- `standards/`: topic landing areas; no specification text is bundled.
- `taxonomies/`: hardware category and relationship definitions.
- `dictionaries/`: canonical attributes, aliases, units, and sensors.
- `identifiers/`: reserved for licensed PCI, USB, and device identifier data.
- `schemas/`: JSON Schemas for normalized hardware records.
- `metadata/`: sources, license review status, and pack versions.

Unknown input columns must remain available as raw attributes. A recognized
alias is a mapping hint, not permission to discard or rewrite its source value.
The analysis response includes a profile with original header names, known
column mappings, unknown headers, inferred categories, and recognized sensors.
The normalized manufacturing record retains raw source values in its
`attributes` map.

## Updating

Review `metadata/sources.json` and `metadata/licenses.json` before adding
external data. Record source version, retrieval date, permitted use, and
redistribution status. Never treat public availability as permission to use
content for model training.
