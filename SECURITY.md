# Security policy

## Supported security model

RowBridge is a local single-user application. It binds to `127.0.0.1` by default and is designed to process files on the machine where it is running.

It is **not** designed to be exposed directly to the public internet or shared as a multi-user service.

Current protections include:

- loopback binding by default;
- trusted-host validation;
- bounded upload reads and input-size limits;
- structural validation for CSV/XLSX input;
- generated staging filenames rather than user-controlled paths;
- automatic staged-file cleanup;
- restrictive Content Security Policy and browser permission headers;
- `Cache-Control: no-store` for application pages;
- Fetch Metadata rejection of browser requests explicitly marked `cross-site`;
- spreadsheet formula-prefix escaping in exports.

These controls reduce accidental exposure and common local-web hazards, but they are not a substitute for authentication, authorization, TLS termination, or hardened reverse-proxy configuration.

## Data handling

Source files are treated as read-only inputs. RowBridge may create temporary staged copies, SQLite state, review history, and explicit exports under its application data directory.

A successful run deletes its staged source copies. Abandoned staging directories expire automatically.

The reconciliation path has no required external API dependency.

## Reporting a vulnerability

Please do not publish a working exploit in a public issue before a fix is available.

Open a GitHub Security Advisory for the repository when available, or contact the repository owner privately through the contact information on the maintainer's GitHub profile.

Include:

- affected version or commit;
- reproduction steps;
- expected and actual behavior;
- impact;
- any suggested mitigation.
