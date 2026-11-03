# Security policy

## Reporting a vulnerability

Please don't open a public issue. Use **Report a vulnerability** on the repository's
[Security tab](https://github.com/ibrahimatmaca/shotforge/security).

Include the affected version, the steps to reproduce it, and the impact you observed.

## Supported versions

Only the latest release gets security fixes.

## Trust model

shotforge runs locally and has no server or telemetry. Treat these as untrusted input:

- Screenshots, because the text in them can try to influence the AI model
- Copy files and templates you didn't create
- AI model output

Screenshots go only to the AI backend you configure. Pre-release app screens are shared with that provider.
