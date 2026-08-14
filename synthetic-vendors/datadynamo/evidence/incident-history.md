# DataDynamo Logistics — Security Incident History

**Synthetic document. Generated for the Drawbridge project.**

Covering the 24 months to 1 February 2026.

## Incident DD-2025-001 — Shipment metadata store exposure

| | |
|---|---|
| Detected | 3 March 2025, 14:20 CET |
| Contained | 4 March 2025, 09:05 CET |
| **Customers notified** | **12 March 2025** |
| **Elapsed time from detection to disclosure** | **9 days** |
| Root cause | A storage bucket holding shipment metadata was configured for public read during a migration. |
| Data affected | Shipment identifiers, origin and destination postcodes, delivery timestamps. No payment data. |
| Remediation | Bucket permissions corrected; a policy check was added to the deployment pipeline. |

## Notes

Disclosure was delayed pending confirmation of the affected record count. The contractual
notification window stated in our customer agreements is 24 hours from detection.

No other security incidents occurred in the period.
