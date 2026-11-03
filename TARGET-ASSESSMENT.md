# A distributed Rust-runtime Linux target

Assessment date: 2026-11-02. This is an investigation, not an implemented target.
The findings combine inspection of the local runtime checkouts with the current
Rust target policy and upstream build sources. Estimates below are engineering
judgment, not commitments from the Rust project.

## Outcome and scope

The desired outcome is a target with a prebuilt standard library and runtime:

```text
rustup target add <approved-target-name>
cargo build --target <approved-target-name>
```

The application should need no Mustang/Eyra dependency, integration macro,
runtime Cargo features, custom target JSON, -nostartfiles flag, or build-std.
The target name is deliberately unspecified: naming and ABI choices need review.

This is feasible in principle, but the current dependency tree cannot simply be
inserted underneath std. Its std dependencies need an explicit bootstrap design.
A target specification handles compiler/linker policy, not dependency injection
or the construction and distribution of its runtime.

Start with x86-64 Linux, a GNU/Linux development host, statically linked native
ELF executables, normal std, native TLS, threads, and both panic strategies.
Other architectures, dynamic linking, sanitizers, and static PIE are subsequent
work. Existing C/assembly dependencies such as ring remain permitted; replacing
libc does not imply all application dependencies must be pure Rust.

## Existing assets

- Mustang already has the target JSON and a rebuild-std workflow. Its target
  selects Linux/GNU behavior and uses vendor=mustang. The current local x86-64
  specification supplies -nostartfiles and forces _Unwind_Backtrace at link time.
- The Mustang integration macro exists to retain its c-gull dependency in the
  final executable. A distributed target must arrange that retention itself.
- Eyra is primarily runtime feature selection and std re-exporting. Its wrapper
  is not a necessary permanent component of a target-owned runtime.
- c-scape implements the lower C ABI layer; Origin implements startup, threads,
  signals, TLS metadata, and exit handling; rustix supplies kernel interfaces.
- The local patches make the application-side stack work on stable Rust 1.99.
  That demonstrates useful runtime functionality, not a bootstrap of std.
- Origin, c-scape, rustix, and rustix-dlmalloc contain some rustc-dep-of-std
  machinery. It is a starting point, not proof that the whole graph is ready.

## The central dependency problem

Today an application can depend on both the normal std and c-gull. Calls from
std to libc symbols resolve to the replacement runtime at final linking.
Cargo does not see that link-time relationship as a Rust dependency cycle.

Adding the current c-gull directly as a dependency of std would create a cycle:

```text
std -> c-gull -> std
```

Concrete local examples:

- c-gull's resolver uses std::process::Command to invoke getent.
- NSS/user/group lookup also uses std process facilities.
- Time handling uses std collections, mutexes, and environment facilities.
- sysconf uses std::thread::available_parallelism.
- Our c-scape stable configuration uses std::thread_local! and explicitly
  enables std. This was appropriate for an application-level dependency, but
  cannot serve unchanged as a dependency below the same std.

The sysroot configuration can retain native #[thread_local] storage rather than
the application-level stable macro. Rust's own standard libraries are built with
compiler-internal facilities; a stable user experience does not require every
implementation crate to build independently on stable.

## Architecture choices

### A. Stage the std and runtime builds, then bundle the runtime archive

First build the target's std as libraries, leaving its C ABI calls to be resolved
at final executable linking. Then build the current runtime against that exact
target std, and package a C ABI static runtime archive in the sysroot. Teach the
target's linker configuration to include it automatically. This is a proposed
two-pass bootstrap, not a verified build. Whether any native-library lookup or
std build step forces earlier runtime availability must be checked in practice.

This preserves the most existing code and is a plausible ergonomics prototype.
Using the same target/compiler/std artifacts may avoid needing a distinct second
std implementation. Using a separately built GNU-target runtime with its own std
is another possibility, but introduces a different and less attractive boundary.
Any runtime archive bundling std raises duplicate runtime-state,
allocator, panic/unwind, symbol-export, binary-size, and recursive-call questions.
There must be a controlled C ABI boundary and an explicit ownership model for
process-wide facilities. It is not safe to assume that two std copies can simply
be linked together. Mustang issue #39 discussed this exact family of approaches.

### B. Make the required runtime usable below std

Keep the Unix C ABI initially, but make the runtime subset required by std depend
only on core/alloc and lower-level libraries. Link it automatically as part of
the sysroot. Broader C compatibility functions may remain in optional layers.

This requires replacing or refactoring the std-based c-gull functions that std
actually calls, and auditing all feature propagation. Native TLS, allocation,
locks, startup, and unwinding must have an acyclic initialization sequence.
Every dependency in that subset needs to use the sysroot's core/alloc identities.

This gives a cleaner long-term dependency graph without immediately rewriting
std's Linux backend, if maintaining the staged build proves too fragile.
It does not require preserving the exact current crate boundaries.

### C. Implement a std backend directly on rustix/Origin

Bypass the C ABI for Rust's own facilities and retain a separate compatibility
layer for C code. This can reduce translation overhead and libc-shaped APIs,
but is a substantially broader std implementation effort. It is not necessary
for the first target prototype. Existing rustix issue #76 explored this direction.

## Compiler, linker, and sysroot work

1. Define the target's ABI and supported execution environment. Decide how to
   identify the runtime through target cfgs. Keeping env=gnu is useful for a
   prototype but inherits glibc assumptions in std, libc declarations, and other
   crates. A distinct environment needs explicit ecosystem support. Audit ABI
   types and constants rather than assuming glibc compatibility is complete.
2. Convert the validated JSON into a built-in rustc target specification. Remove
   claims for unsupported sanitizers/dynamic linking. Specify CPU/kernel baseline,
   static linking, TLS, relocation, linker, and startup policies deliberately.
3. Make ordinary binaries automatically receive the runtime and entry point.
   Account for archive extraction, dead-code elimination, LTO, test harnesses,
   panic=abort and panic=unwind, custom global allocators, and no_std binaries.
   Proc macros and build scripts must continue to build for the host normally.
4. Replace c-scape's empty libc/libpthread/libgcc archive workaround with explicit
   target link rules. Package real startup/runtime artifacts where needed. Avoid
   accidentally finding host libc, startup objects, or GCC unwinding libraries.
5. Connect std's allocator and unwind components to the chosen runtime. The Unix
   System allocator currently calls libc allocation functions; these must reach
   the runtime's allocator without a global-allocator recursion loop. The Rust
   unwinding implementation must satisfy std's ABI without selecting libgcc_s.
6. Add runtime build steps and installation to Rust bootstrap and distribution.
   Ship target core, alloc, std, panic/unwind components, required runtime archives,
   and startup objects as applicable. Artifacts must match the exact compiler
   release. Upstream musl support demonstrates target-owned self-contained CRT
   configuration, but does not supply a ready-made builder for this Rust stack.
7. Coordinate libc/cc target recognition and test real crates, including C code
   compiled by ring. Headers, ABI expectations, and linker selection matter even
   when the runtime implementation itself is Rust.

## Runtime completeness and testing

The present six application/runtime tests are useful smoke tests, not evidence
of complete std or C-library compatibility. Build a target test matrix including:

- std and libtest tests; startup/exit; constructors; TLS and destructor ordering;
  thread creation/join/detach; synchronization; signal handling; stack guards.
- Allocation, alignment, custom global allocators, panic catching and unwinding,
  child-process behavior, pipes, environment, filesystem and socket operations.
- Numeric and hostname networking, DNS, user/group lookup, and time zones.
  The current external-getent requirement is a runtime dependency even though
  the executable is statically linked. Decide whether to support that dependency
  or provide self-contained resolution. It is not automatically disallowed by
  target policy, but must be explicit and tested.
- Debug/release/LTO, representative real crates, native-code dependencies, and
  link-map/ELF checks for unintended system runtime dependencies.

Our choice of fixed-address static ELF is sufficient for a prototype. Static PIE
requires separate validation of Origin's relocation implementation; changing the
default to PIE without doing that would invalidate the current assumptions.

## Distribution and maintenance

Tier 3 recognition and official binary distribution are different milestones.
Tier 3 permits a built-in target without guarantees that it builds. The normal
route to Rust-distributed prebuilt std artifacts is Tier 2. Adding the target
definition alone does not create a rustup-installable std component.

Rust's policy requires designated maintainers, staged promotion, team review,
and reliable builds. Tier 2 requires at least two target maintainers, public
utility, compiler/infrastructure approval, and no significant avoidable gaps in
the standard library. Host tools on this runtime are not necessary for the
initial cross-compilation target. Promotion normally spans multiple stable
releases. Acceptance cannot be guaranteed by a working prototype.

Before official distribution, a separately released custom toolchain can give
users the intended application workflow with cargo +<toolchain> build --target
<target>. rustup toolchain link supports local custom toolchains. This is not
the same as registering a third-party target for rustup target add on stock
stable; an installer/release process would still be ours to maintain.

An upstream target improves integration and catches build breakage, but does not
transfer maintenance of archived runtime libraries to Rust's compiler team. A
maintained fork, releases, ownership, and security/compatibility work remain
necessary. The same distinction applies to an externally maintained libc such
as musl.

## Suggested next experiment and effort

First test the two-pass same-target std/runtime build from option A. If viable,
produce a pinned custom toolchain/sysroot for x86-64 Linux that builds an
unmodified hello-world Cargo project with just --target: no application runtime
dependency, macro, build script, or build-std. Then run the unchanged application
and its tests against it. This isolates the missing integration from upstream
acceptance and distribution questions.

For planning, allow several engineer-weeks for that prototype, with high
uncertainty around the archive/bootstrap approach. An acyclic runtime suitable
for sustained std integration is a several-person-month project, potentially
longer once completeness and upstream review are included. These are orders of
magnitude, not estimates derived from a completed sysroot build. Tier promotion
has additional release-cycle and organizational lead time.

The immediate design gate is choosing and proving the bootstrap architecture.
The technical success criterion is not merely an accepted target JSON: it is a
prebuilt, automatically linked std/runtime that works in an unmodified project.

## Primary references

- [Target tiers and distribution](https://doc.rust-lang.org/rustc/platform-support.html)
- [Target admission and promotion policy](https://doc.rust-lang.org/rustc/target-tier-policy.html)
- [Adding a target](https://rustc-dev-guide.rust-lang.org/building/new-target.html)
- [Mustang official-target design discussion](https://github.com/sunfishcode/mustang/issues/39)
- [Porting std to rustix](https://github.com/bytecodealliance/rustix/issues/76)
- [std dependency manifest](https://github.com/rust-lang/rust/blob/master/library/std/Cargo.toml)
- [Unix System allocator](https://github.com/rust-lang/rust/blob/master/library/std/src/sys/alloc/unix.rs)
- [Unwinder selection](https://github.com/rust-lang/rust/blob/master/library/unwind/src/lib.rs)
- [musl target defaults](https://github.com/rust-lang/rust/blob/master/compiler/rustc_target/src/spec/base/linux_musl.rs)
- [CRT object configuration](https://github.com/rust-lang/rust/blob/master/compiler/rustc_target/src/spec/crt_objects.rs)
- [Distribution build steps](https://github.com/rust-lang/rust/blob/master/src/bootstrap/src/core/build_steps/dist.rs)
- [Custom toolchains](https://rust-lang.github.io/rustup/concepts/toolchains.html#custom-toolchains)
