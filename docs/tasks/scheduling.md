# Scheduling

[← Benchmark overview](../../README.md)

A shortened synthetic dev example. Resolve the supplied MRN and cite supporting records; resource labels are abbreviated.

## Move an appointment and update capacity · RU

**Request:** “The patient cannot travel on the original date. Move the appointment to the listed free slot at the same clinic, release the old slot and reserve the new one.”

| Resource | Relevant retrieved information |
|---|---|
| `Appointment/visit` | Booked for June 17, 2023, 10:00–10:30 UTC; references the old slot |
| `Schedule/clinic` | Clinic schedule and actors shared by the relevant slots |
| `Slot/old` | `status: busy`; original appointment interval |
| `Slot/new` | `status: free`; June 20, 2023, 10:00–10:30 UTC |

**Chart change:** In one transaction, move the Appointment start/end and slot reference, set the old Slot to `free`, and the new Slot to `busy`. Use current versions; preserve participants and unrelated fields. Other unavailable slots remain untouched.

**Expected answer:** `{"appointment_status": "booked", "start": "2023-06-20T10:00:00Z", "old_slot_status": "free", "new_slot_status": "busy"}`.

<!-- Source: dev task 9b61c7adbb7ea22cada74529; family reschedule_with_slot_release. -->

## Record the patient's declined reservation · RU

**Request:** “The verified caller declines their booked laboratory follow-up. Record the patient response, cancel the visit and release its reserved capacity.”

Retrieve the `Appointment`, its `Slot`, and the patient's `AppointmentResponse`. In one transaction, set the appointment to `cancelled`, its patient participant and response to `declined`, and the slot to `free`. Preserve all other fields and use current versions.

**Expected answer:** `{"cancelled": true, "response_recorded": true}`. A patient response and the clinic's appointment state are separate records; changing one does not complete the whole task.

<!-- v0.4.0 family: patient_declines_appointment. -->
