# Third-party sources

Synthetic Hospital v1.3: https://github.com/sparkcpark/synthetic_hospital, commit `911f34c4ac65a508543c4b3b90c373a0cd16534d`, Christine Park, Valerie Chen and Tim Dettmers. The source code is MIT. The released data card describes fully synthetic redistributable data and separately discusses terminology licensing. Its exact LICENSE is retained in the HF release preparation. The importer excludes the ontology tables.

FHIR R4 4.0.1 JSON schema: https://hl7.org/fhir/R4/fhir.schema.json.zip. Copyright HL7; FHIR specification licensing: https://hl7.org/fhir/R4/license.html. The schema file is distributed verbatim in this research package. The local validator checks JSON shape and selected graph constraints; it does not claim complete conformance.

LOINC example 718-7 (hemoglobin): https://loinc.org/718-7/. LOINC is copyright Regenstrief Institute, Inc. and the LOINC Committee; see https://loinc.org/license/. The benchmark uses selected example codes and does not redistribute a terminology database.

Generation v0.4.0 also distributes definition-derived structural contracts in `resource_contracts.json`, extracted from the official `hl7.fhir.r4.core#4.0.1` package. Each source StructureDefinition has a recorded SHA-256. These are cardinality, choice and reference-target facts from the specification, rather than the complete source definitions. They follow the FHIR specification attribution and licensing above. The compiler reuses the pilot's attributed measurement patterns.

FHIR concepts and local benchmark codes are used for simulation. Local codes have no asserted mapping to clinical terminology. The user's generated derivative currently has no newly assigned public license.

Generation pilot: `generation_patterns.json` contains selected shapes, terminology examples and root contracts from official FHIR R4 4.0.1 examples and StructureDefinitions. Source URLs and SHA-256 hashes are retained. See [FHIR licensing](https://hl7.org/fhir/R4/license.html), [blood-pressure example](https://hl7.org/fhir/R4/observation-example-bloodpressure.json.html), and [lipid-result example](https://hl7.org/fhir/R4/diagnosticreport-example-lipids.json.html). Runtime blood-pressure components use only the example's LOINC codings. Lipid examples use LOINC 35200-5 and 35217-9 with UCUM units; LOINC attribution and licensing above apply. The official validator JAR and downloaded definitions remain local artifacts rather than redistributed package files.
