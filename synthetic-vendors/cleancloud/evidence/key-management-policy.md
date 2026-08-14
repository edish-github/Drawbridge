# CleanCloud Analytics — Key Management and Retention Policy

**Synthetic document. Generated for the Drawbridge project.**

Version 3.1 · Effective 1 January 2025 · Owner: Head of Information Security

## 1. Encryption standards

| Data state | Standard |
|---|---|
| At rest | AES-256-GCM, keys held in a cloud key management service |
| In transit | TLS 1.3; TLS 1.2 permitted only for named legacy integrations, of which there are currently none |
| Backups | AES-256-GCM with a separate key hierarchy |

## 2. Key lifecycle

Keys are generated inside the key management service and are never exported. Rotation occurs
every 90 days and is automated. Rotation events are logged and retained for 24 months.

Key access is restricted to two service identities and no human account.

## 3. Retention

| Data category | Retention |
|---|---|
| Operational records | 24 months |
| Report exports | 12 months |
| Audit and access logs | 24 months |
| Backups | 35 days beyond the primary retention period |

Deletion runs nightly against primary storage. Backup expiry follows within 35 days, which is
the point at which a record is unrecoverable.
