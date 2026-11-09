# Contributing to DropNet

DropNet is an experimental source release under GPL-3.0-or-later. Contributions
to original project files use the same license; identify the provenance and license
of third-party material. See COPYING.md and THIRD_PARTY_NOTICES.md.

## Start with a concrete question

Read the [manifesto](MANIFESTO.md) and [roadmap](ROADMAP.md). Open an issue describing
the problem, who encounters it, possible alternatives and a way to compare them.
Technical criticism and suggestions to reuse existing projects are welcome.

Useful contributions include Android feasibility work, protocol comparisons,
threat modelling, interface proposals and improvements to the documentation.

## Propose changes

Keep each pull request focused. Explain the problem, the change, supporting evidence
and any effect on protocol behaviour or interpretation of results. Distinguish
what you tested from what remains hypothetical.

Do not silently change units, expiry rules, traffic limits, message selection,
loss assumptions or delivery criteria. Preserve original experimental inputs and
outputs. Implementation contributions must include reproducible validation instructions
and relevant regression tests. Run the commands in README.md before proposing a release.

## Report an experiment

Record the exact version, devices or runtime, steps, expected outcome and observed
result. Use synthetic messages. Remove personal paths, identifiers and private
content from diagnostic material before posting it.

Receipt by an intermediate relay does not demonstrate delivery to the recipient.
Simulation outcomes do not establish radio range, battery life or real-world safety.

## Collaboration

Discuss ideas respectfully, credit other people's work and explain assumptions.
See the [governance proposal](GOVERNANCE.md) for how decisions and responsibilities
could be shared as the project grows.

Do not post credentials or exploitable vulnerability details in a public issue.
Follow SECURITY.md for the private reporting channel and current scope.
