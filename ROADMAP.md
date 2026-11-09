# Proposed roadmap

Milestones describe results to work toward, not release dates or completed work.

## 1. Prepare the source release

- Select explicit licenses and review the provenance of the files to be released.
- Package the simulator, tests, specification and required regression fixtures.
- Reproduce the tests and full experiment campaign from the release package.
- Establish a private security reporting channel.

Completion means another person can reproduce the documented checks from the
published files without private coordination records or unlisted local inputs.

## 2. Compare existing approaches

Document which components can be reused. Compare neighbour discovery, delayed
forwarding, persistence, trust assumptions and Internet dependencies. Cite exact
versions and distinguish documentation from independently reproduced behaviour.

Completion means a justified choice of components and a list of unresolved questions.

## 3. Demonstrate a persistent relay on devices

A creates a message for C while only B is reachable. B stores it. A goes offline,
B restarts, and a later encounter between B and C delivers the message.

Record compatible devices, operating systems, permissions, delivery evidence,
actual transferred bytes and timing. Extend the experiment to background operation
and energy measurement. Use synthetic content while protections are incomplete.

## 4. Design and review protections

Specify adversaries, keys and contact verification, metadata exposure, spam limits
and behaviour after device loss. Use established cryptographic components.
Security work can begin alongside the earlier milestones; protected personal
communication requires implemented and reviewed protections.

## 5. Organize small local pilots

Once a device prototype is usable, recruit groups able to meet physically.
Document participation and pause controls, diagnostic collection and test procedures.
Separate message creation, relay receipt, confirmed recipient delivery and expiry.

Completion means repeatable reports from independent groups, including failures
and limitations, rather than download or follower counts alone.
