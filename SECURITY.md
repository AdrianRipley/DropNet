# Security and responsible reporting

DropNet is an experimental demo, not a secure messenger. The current wire format
has no encryption or authentication. Use synthetic messages only. Do not rely
on it for emergencies or sensitive communication. See docs/THREAT_MODEL.md.

For non-public vulnerability reports, email the project owner at
**adrian.ripley@proton.me**. Include the affected revision, a minimal reproduction
with synthetic data, and the expected versus observed behavior. Do not send
credentials, real message contents or unnecessary personal data.

Do not publish exploitable details in a public issue before the maintainer has
had an opportunity to assess them. No response-time or security-fix SLA is
promised by this early volunteer project.

Reports about absent cryptographic protection are already within the documented
limitations. Parser crashes, unbounded resource use, incorrect persistence and
misleading delivery acknowledgments are useful concrete findings.
