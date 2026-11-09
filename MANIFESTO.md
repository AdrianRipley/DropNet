# Proposed manifesto

This is a proposal for discussion. It describes the direction of DropNet, not
features already implemented or rules already adopted by a community.

## Devices helping people communicate

We want participating devices to carry messages for one another, including when
Internet and cellular connections are unavailable. Communication should not require
permission from a mandatory central operator.

## Voluntary participation

People should control whether their devices participate and how much storage,
traffic and energy they contribute. Devices without compatible forwarding software
and consent are not assumed to be relays.

## Local autonomy

Groups should choose their contacts and rules. The protocol should not require a
central identity registry or an authority that approves each communication.

## Open implementation

We intend to publish code and specifications under explicit free licenses, document
formats, support compatible implementations and make forks possible. The concrete
licenses still need to be chosen. We should reuse existing work where appropriate
and contribute improvements back when possible.

## Verifiable trust

People need ways to verify contacts and message authenticity. A signature can
establish a relationship to a key; it cannot establish whether a claim is true.
Spam, multiple identities and malicious relays require explicit design work.

## Privacy demonstrated by evidence

Encryption, authentication, metadata protection and anonymity are distinct
properties. Each needs design and evaluation. The current local prototype does
not implement these protections and must use synthetic messages.

## Resistance with honest limits

Resistance to censorship is a design goal. We do not promise communication that
cannot be disrupted. Isolation, radio interference, unavailable devices and message
expiry can prevent delivery.

## Shared responsibility

Contributors should be able to question decisions, propose alternatives and take
on responsibility. Roles and decision processes should be explicit. Moderation
of collaboration spaces must allow disagreement while addressing harassment and
disclosure of personal information; it is not central approval of network traffic.

## Reproducibility before promises

We distinguish simulations, local process tests and measurements on real devices.
We document assumptions, failures and repeatable procedures. Our first physical
milestone is a three-device experiment with a persistent intermediate relay.
