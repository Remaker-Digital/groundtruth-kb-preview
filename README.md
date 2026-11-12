# GT-KB pre-release preview

> **Pre-release preview.** This repository gives a look at GT-KB before its release. It is not a
> release. There is no supported install, and nothing here is offered for production use.

GT-KB is a verified-work system for actions, non-code artifacts, and code.
It supports specifying artifacts and operations, executing them according to a plan, and verifying
them at multiple stages from conception to verified completion. AI-assisted software development is a
primary use case.

GT-KB's canonical knowledge and work store is Membase. The separate legacy membase research repository
implements Membase (SQLite). GT-KB's Membase and Membase (SQLite) are different implementations of
Membase.

## What is in this preview

| Path | What it is |
| --- | --- |
| [docs/reader-path.md](docs/reader-path.md) | One planned handoff, followed step by step. Start here. |
| [docs/architecture.md](docs/architecture.md) | The target architecture: sections 1 to 11 of GT-KB's System Architecture Document. |
| [modules/gtkb-protocol](modules/gtkb-protocol) | The storage-free protocol library: canonical JSON, protocol types and an explicit HTTP client. Source candidate 0.1.0. |
| [modules/gtkb-authority](modules/gtkb-authority) | The PostgreSQL authority: schema, domain transactions and control interfaces. Source candidate 0.1.0. |

## How GT-KB is being built

GT-KB is being built as five modules:

- **gtkb-protocol**: storage-free protocol, canonical serialization and typed client. In this preview.
- **gtkb-authority**: PostgreSQL authority, governed domain operations and operational controls. In this
  preview.
- **gtkb-execution**: execution adapters, process custody and truthful execution observations.
- **gtkb-context**: authored guidance, context composition and attributable retrieval.
- **gtkb-home**: selected-root setup, the Home interface and workstation lifecycle.

The last three do not have accepted source yet, so they are not in this preview.

## Status

- The documents state design intent. They do not describe released or installed behavior.
- The module source here is a candidate under independent review. It is not an accepted or released
  artifact, and it has no published package.
- A production release is a separate milestone with its own installation and verification
  requirements.
- This preview is updated by hand at milestones. Its history starts fresh and is not the project's
  development history.

## License

This is source-available software. It is not open source.

- Original software is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).
- Original documentation and other media are licensed under the
  [Creative Commons Attribution-NonCommercial 4.0 International license](LICENSE-DOCUMENTATION).

Both allow noncommercial use, copying, modification and sharing. Commercial use is reserved.
Third-party components keep their own licenses. See [NOTICE](NOTICE).

To ask about GT-KB or commercial use, email mike@remakerdigital.com.

Required Notice: Copyright 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC.
