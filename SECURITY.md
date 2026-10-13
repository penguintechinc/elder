# Security Policy

## Supported Versions

Elder follows the release-branch model in `release/v{Major}.{Minor}.X` — each branch
accumulates patch releases until superseded by the next minor. Security fixes are
backported to the two most recent minor lines.

| Version         | Supported          |
| --------------- | ------------------- |
| 4.0.x (current) | :white_check_mark:  |
| 3.2.x           | :white_check_mark:  |
| < 3.2           | :x:                 |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report privately to **security@penguintech.io** with:

- What you found and where (file/endpoint/component)
- Steps to reproduce
- Potential impact

We will acknowledge your report within **24 hours** and aim to ship a fix as soon as
possible, prioritized by severity. We will coordinate disclosure timing with you and,
if you'd like, credit you in the release notes / security advisory once a fix ships.

A machine-readable version of this contact is published at
[`/.well-known/security.txt`](.well-known/security.txt) per [RFC 9116](https://www.rfc-editor.org/rfc/rfc9116).

## Scope

This policy covers the Elder application (`apps/`, `shared/`, `web/`) and its official
container images published to `ghcr.io/penguintechinc/elder-*`. Third-party dependency
vulnerabilities should be reported upstream as well as to us if they affect Elder's
default configuration.
