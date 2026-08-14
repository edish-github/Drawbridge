# CleanCloud Analytics Ltd — Service Organisation Control Report

**Synthetic document. Generated for the Drawbridge project. Not a real audit report and not
issued by any real audit firm.**

| | |
|---|---|
| Service organisation | CleanCloud Analytics Ltd |
| Report type | Type II |
| Report period | 1 January 2025 – 31 December 2025 |
| Auditor | Harlow & Finch LLP (fictional) |
| Opinion | Unqualified |
| Report date | 12 February 2026 |

## 1. Scope

This report covers the CleanCloud Reporting service and the underlying data platform,
including the standard tier. The internal staging environment is explicitly out of scope; it
holds no customer data and is populated with generated fixtures.

## 2. Trust services criteria covered

Security, Availability and Confidentiality. Processing Integrity and Privacy were not in
scope for this period.

## 3. Control environment

CleanCloud operates a documented information security management system certified to
ISO/IEC 27001:2022. Security responsibility sits with the Head of Information Security, who
reports to the board quarterly.

## 4. Access control

Multi-factor authentication is enforced for all personnel across all systems, including
administrative access to production. No standing exemptions exist. Two break-glass accounts
are held by named directors; both require hardware tokens, and every use generates an alert
reviewed within one business day.

Privileged access is granted through a request-and-approval workflow with a named approver
and is time-bound to eight hours. Quarterly access reviews were performed on 31 March,
30 June, 30 September and 31 December 2025. All four completed on schedule with no
outstanding items.

## 5. Exceptions and observations

**No exceptions were identified during the reporting period.**

Two observations were raised and both were remediated within the period:

- **Observation 1 (raised 14 March 2025, closed 2 April 2025).** Log retention for the
  staging environment was configured at 30 days against a documented standard of 90. The
  staging environment holds no customer data. Configuration corrected and verified.
- **Observation 2 (raised 8 August 2025, closed 21 August 2025).** Two offboarded contractor
  accounts were disabled but not deleted within the documented 30-day window. Both were
  confirmed to have had no access after disablement. Process automated and verified.

## 6. Incident history

No security incidents affecting customer data occurred during the reporting period.

One availability incident occurred on 4 February 2025, lasting 41 minutes, caused by a failed
deployment to the reporting API. Service was restored by rollback. No data was exposed and no
customer notification was required under the contractual threshold.

## 7. Business continuity

The recovery time objective for the reporting service is 4 hours and the recovery point
objective is 15 minutes. A full disaster recovery test was performed on 11 November 2025.
Recovery completed in 2 hours 51 minutes with a measured data loss window of 6 minutes. Both
objectives were met.

## 8. Encryption

Data at rest is encrypted with AES-256 using keys managed in a cloud key management service.
Data in transit is encrypted with TLS 1.3. Keys are rotated every 90 days; rotation events
were sampled for four quarters and all were within schedule.

## 9. Subprocessors

Three subprocessors were in use throughout the period. None process customer content. See the
attached subprocessor list for purposes and data categories.
