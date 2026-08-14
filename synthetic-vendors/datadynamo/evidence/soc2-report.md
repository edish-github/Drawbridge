# DataDynamo Logistics GmbH — Service Organisation Control Report

**Synthetic document. Generated for the Drawbridge project. Not a real audit report and not
issued by any real audit firm.**

| | |
|---|---|
| Service organisation | DataDynamo Logistics GmbH |
| Report type | Type II |
| Report period | 1 July 2024 – 30 June 2025 |
| Auditor | Kestrel Assurance Partners (fictional) |
| Opinion | Qualified |
| Report date | 29 August 2025 |

## 1. Scope

This report covers the DataDynamo Routing Platform, including the enterprise tier, and the EU
processing environment.

## 2. Trust services criteria covered

Security and Availability. Confidentiality was not in scope for this period.

## 3. Control environment

DataDynamo operates a documented information security programme. Security responsibility sits
with the Compliance Manager, who reports to the Chief Operating Officer.

## 4. Access control

Multi-factor authentication is deployed across the standard user population. Coverage was
tested by sampling 40 accounts across the platform, of which 34 were enrolled.

Privileged access to the routing platform is granted by the platform team on request. Access
is reviewed periodically; the review cadence was not documented at the time of testing.

## 5. Exception notes

Two exceptions were identified during the reporting period.

### Exception 3.1 — Access review timing

**Status: remediated.** Two of the four quarterly access reviews in the period were completed
after their scheduled date, by 11 and 19 days respectively. A calendar-driven reminder process
was implemented in April 2025 and the subsequent review completed on schedule.

### Exception 3.2 — Multi-factor authentication coverage for administrative access

**Status: unremediated at the end of the reporting period.**

Testing identified that multi-factor authentication is **not enforced for administrative
access** to the routing platform's production console. Six of the forty accounts sampled held
administrative privileges, and none of the six were enrolled in multi-factor authentication.
Access to these accounts is protected by password and network restriction only.

Management acknowledged the finding and stated an intention to enrol administrative accounts
in the next planning cycle. **No remediation date was provided, and no remediation had been
performed as at the end of the reporting period.**

The service auditor's opinion is qualified in respect of this exception.

## 6. Incident history

One security incident occurred during the reporting period. See the attached incident history
for detection and disclosure dates.

## 7. Business continuity

The stated recovery time objective is 8 hours and the recovery point objective is 1 hour. No
disaster recovery test was performed during the reporting period. The most recent test on
record was completed in October 2024, before this period began.

## 8. Encryption

Data at rest is encrypted using platform-managed keys. Data in transit is encrypted using TLS.
Specific standards, key lengths and rotation intervals were not documented and could not be
tested.
