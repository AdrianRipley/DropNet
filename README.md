# DropNet

**Exploring communication carried by participating devices.**

DropNet is an early project initiated by **Adrian Ripley**. Its goal is to let
people exchange short text messages through compatible, voluntarily participating
devices without mandatory Internet access, cellular service or central servers.

A device would keep a message when its recipient is unreachable, carry it while
moving, and forward it during a later encounter. Delivery depends on a suitable
sequence of contacts: disconnected communities cannot communicate without a path.

## Current stage

This initial repository contains the project's proposed principles, roadmap and
collaboration guidelines. The reference implementation is being prepared for a
separate source release; it is not included here yet.

Existing local work consists of a Python reference simulator and a multi-process
demo using UDP on one computer. It does not implement Bluetooth, encryption or
authentication. No phone application, radio range measurement or energy measurement
is available. DropNet is not ready for sensitive messages or emergency use.

## Help shape the project

- Review the [proposed manifesto](MANIFESTO.md).
- Explore the [roadmap and first tasks](ROADMAP.md).
- Read [how to contribute](CONTRIBUTING.md) and the [proposed governance](GOVERNANCE.md).
- Use issues for concrete proposals, questions and reproducible problems.

We welcome help with Android experiments, distributed protocols, security review,
reproducibility, documentation and accessible interfaces. Critical comparisons
with existing projects are valuable, including proposals to reuse their work.

## Intended development model

The goal is free and open-source development, with explicit licenses, public
technical decisions and independently implementable protocols. **A release license
has not been selected yet.** This repository does not currently grant an explicit
open-source license. Licensing and source provenance are part of the first milestone.

GitHub is a collaboration tool for development, not a proposed requirement for
devices or users to participate in the communication protocol.
