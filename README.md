# rust-linux-raw

The goal of this little project is to provide the ability to create static rust
binaries in a way that is easy for developers to use.

The main reason I started looking at this was that it is very convenient when
building services that are packaged and released in container images be able to
build those images on top of very bare bones container images. I was frankly 
a bit jealous of the Go community, where building static binaries is more-or-less
the norm.

When I started to look at this problem space, I very happy and a bit surprised
to realise that there was code out there to replace most of the needed libc
functionality in the rustix, Origin and c-ward projects. However, there were
some rough corners that made it a bit of a challenge to use.

There are a few things that makes this different from previous efforts:

* It targets recent, stable rust.
* For now, you download a separate set of toolchain binaries which works like
  upstream, but generate static output that doesn't depend on any c libraries


## Caveats

* This is early, experimental code. Don't come after me if things are broken. 
* For now, the goal is to support rust-only builds. Linking in C code may or
  may not work.
* DNS resolution is supported through calling the `getent` binary. If
  that binary is not around, things will probably fail
* Trusting some random person on the internet with binaries that you use
  to build your software is risky and not adviced. I will soon set up something
  that uses GitHubs publicly available build infrastructure to produce the
  binaries, which gives a little bit more reassurance but this is not a good
  long term solution. 
* For now, the resulting binaries are not doing address randomisation, a whole
  class of security vulnerabilities are eaiser to exploit compared to the libc
  or musl variants.

## FAQ

### Why not use the `*-unknown-linux-musl` target instead?

Good quesetion, it is a mature solution to a similar problem, and probably 
the soultion I would recommend for anyone that just want static linux binaries
right now. The resaon I wanted to look into a pure rust implementaion of this
is mostly curiosity and a wish to be able to use a better language and ecosystem
when doing systems programming. My other main free software contribution in this
space, [pam-ssh-agent](https://github.com/nresare/pam-ssh-agent) was born 
out of the frustration that the limitations of C resulted in technically inferior
code that is difficult for people to contribute to. I have also always wanted 
to learn more about the low level stuff that something like a libc implementation
provides.

### What are the short term plans for this

Have reproducible builds through a buildsystem. Get to a place where the upstream
crate dependencies are not archived and read-only. Support arm64.
Look at implementing dns lookups without relying on the `getent` binary.

### What are the longer term plans?

I would love for this to be integrated one way or another into the upstream.
Introducing it as a Tier 3 target doesn't seem like an unsurmountable amount of
work. I think that it might also unlock changes to `std` moving it away from
using the libc defined interface to lower level functionality which in turn
might reduce the amount of unsafe code and bring a whole lot of positive second
order effects.

### How do I build the toolchain myself?

I have tested this on Ubuntu 26.04, build by running `python3 build.py`
