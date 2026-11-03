# rust-linux-raw

An experiment toward a Rust runtime and standard-library target for Linux without
an external system libc, building on rustix, Origin, and c-ward.

The first goal is a working toolchain people can try; upstream Tier 3 target
support is a possible later step. Proposed target names are
`x86_64-unknown-linux-raw` and `aarch64-unknown-linux-raw`.

This repository currently contains planning documentation, not a working toolchain.
See [TARGET-ASSESSMENT.md](TARGET-ASSESSMENT.md) for the technical assessment.
