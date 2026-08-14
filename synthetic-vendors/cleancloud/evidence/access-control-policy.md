# CleanCloud Analytics — Access Control Policy

**Synthetic document. Generated for the Drawbridge project.**

Version 4.2 · Effective 1 January 2025 · Owner: Head of Information Security

## 1. Authentication

Multi-factor authentication is mandatory for every user account on every system, without
exemption. This includes administrative access to production, access to the cloud console,
and access to the source repository.

Break-glass accounts are the only accounts exempt from the standard authentication flow. Two
exist. Both require a hardware token, both are held by named directors, and every use raises
an alert reviewed within one business day.

## 2. Privileged access

Privileged access is requested through the access workflow, approved by a named approver who
is not the requester, and granted for a maximum of eight hours. Standing privileged access is
not issued.

## 3. Access reviews

All access is reviewed quarterly by the system owner. Reviews are evidenced in the access
management system and sampled during the annual audit.

## 4. Joiners, movers and leavers

Access is provisioned on the first working day and revoked within four hours of a confirmed
departure. Accounts are disabled immediately and deleted within 30 days.

## 5. Production data access

Staff access to customer data in production requires a ticket referencing the customer
request, is approved by the Head of Information Security, is limited to eight hours, and is
logged with the ticket reference.
