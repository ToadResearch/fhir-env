# Records

[← Benchmark overview](../../README.md)

A shortened synthetic dev example under an explicitly fictional records-release policy. Resolve the supplied MRN and cite supporting records.

Read each example from **request → retrieval path → chart action → expected answer**. Arrows show retrieval dependencies, not a required number of API calls. Expected JSON shows answer fields; full submissions also include retrieved-resource citations.

## Check release scope and route expired authorization · CR

### Request

> The receiving practice requests a transfer summary. Check the named recipient and whether authorization covers today, June 14, 2023. Create the appropriate staff-review task; do not send records.

### Retrieval path

```text
MRN ---> Patient ---> CommunicationRequest
                            |
                 +----------+----------+
                 v                     v
              Consent          DocumentReference
              recipient        prepared summary
              + validity
                 |
                 v
            Organization
            intended recipient
                 |
                 v
         authorization expired ---> create staff-review Task
                                    send no records
```

### Retrieved evidence

- `CommunicationRequest/transfer` — Request to prepare the transfer summary after checking authorization
- `Consent/authorization` — `status: active`, but `provision.period.end: 2023-06-12T10:00:00Z`; recipient reference
- `Organization/recipient` — Receiving practice named in the authorization
- `DocumentReference/summary` — Prepared coordination summary; does not itself authorize release

### Chart action

Create a requested Task linked to the transfer request: “Obtain updated records-release authorization before preparation.” Preserve the Consent and documents; make no external disclosure.

<!-- Source: dev task f554e7452c250604ad7a3f0b; family records_release_scope_review. -->

### Expected answer

```json
{
  "authorization_current": false,
  "release_sent": false
}
```

An active Consent status does not override its expired scope.

## Discover a transfer packet and log transmission · R / CRU

### Request

> Open the supplied transfer handoff identifier. Find its care episode, team, packet list and manifest, then read each requested document. The coordinator now confirms transmission; record that event and complete the handoff.

### Retrieval path

```text
MRN ---> Patient ---> handoff Task (supplied identifier)
                       /              |                \
                    focus          input refs          input ref
                     |                |                    |
                     v                v                    v
               EpisodeOfCare         List           DocumentManifest
                     |                |                    |
                     v                v                    v
                  CareTeam     DocumentReference(s)  grouped contents
                                      |
                                      v
                               read packet documents
                                      |
                       coordinator confirms transmission
                                      |
                                      v
                              ONE TRANSACTION
                       complete Task + create Communication
                       receipt remains unconfirmed
```

`Task.input → List.entry → DocumentReference` identifies the packet contents. `Task.focus → EpisodeOfCare.team → CareTeam` identifies the coordination context. `DocumentManifest.content` describes the grouped documents.

### Chart action

Atomically complete the Task and create a Communication about the manifest. Record transmission; leave receipt unconfirmed. Preserve the original documents and episode history.

<!-- v0.4.0 families: transfer_packet_search, record_transfer_transmission. -->

### Expected answer

```json
{
  "document_count": 3,
  "episode_status": "active",
  "receipt_documented": false
}
```

The read-only variant stops after retrieving the packet.
