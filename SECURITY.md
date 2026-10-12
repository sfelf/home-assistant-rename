# Security policy

`ha-rename` works with Home Assistant long-lived access tokens, which carry
administrator rights. Security reports are taken seriously.

## Reporting a vulnerability

**Please don't report security problems in a public issue, discussion or pull request.**

Report them privately through GitHub instead:
[**Report a vulnerability**](https://github.com/sfelf/home-assistant-rename/security/advisories/new)
(also under the repository's **Security** tab).

Please include:

- what the problem is and what an attacker could do with it
- steps to reproduce, or a proof of concept
- the `ha-rename` version (`ha-rename --version`), your OS and Python version

This is a volunteer project, so reports are handled on a best-effort basis. You'll get
a reply in the private report. Once a fix is released, the advisory is published with
credit to you unless you'd rather stay anonymous.

## What's in scope

For example:

- the access token being exposed: printed, logged, written to disk outside the
  system keyring, or sent anywhere other than the configured `--ha-url`
- the token for one Home Assistant URL being used for a different one
- changes being made to Home Assistant during a `--dry-run`, or to entities that
  don't match `--old-id-prefix`

Out of scope: problems in Home Assistant itself (report those to the
[Home Assistant project](https://www.home-assistant.io/security/)), in your
operating system's keyring, or that need someone who already controls your computer
or user account.

## Supported versions

Fixes go into the latest release only. Please check that a problem still occurs on
the [latest release](https://github.com/sfelf/home-assistant-rename/releases/latest)
before reporting it.
