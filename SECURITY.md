# Security Policy

## Threat model — read this first

Akouo has **no authentication and no transport encryption**, by design and not by accident. It was
built as an appliance for one trusted network: a tablet in the sound booth talking to a Mac in the
same building.

Concretely, anyone who can reach port 8000 can stop a recording, start or stop the livestream,
delete markers, change settings and download every recording. Anyone who can reach port 8001 can
do the same to the audio service directly.

**Do not expose either port to the internet.** If you need access from outside the building, use a
VPN or a mesh network such as Tailscale or WireGuard — not a port forward, and not a reverse proxy
without authentication in front of it.

Further things worth knowing before you deploy:

- Running reliably on macOS wants sleep disabled and auto-login enabled. Both weaken the host, and
  auto-login in particular means an unattended desktop session.
- The database holds service themes, transcripts and AI summaries of sermons. Depending on your
  jurisdiction that is special-category personal data — see the privacy section in the README.
- Session folders under the recordings directory hold full-length WAVs and transcripts with no
  access control beyond filesystem permissions.
- The NAS service account should have access to exactly one share. If it can read user home
  directories, it can read every user's private files.

## Supported versions

Only the current `main` branch. There are no backported fixes and no long-term support branches;
this is a single-deployment project published for reuse.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private vulnerability reporting on this repository (Security → Report a vulnerability),
which reaches the maintainer directly and stays non-public until resolved.

What to expect: an acknowledgement within a week or two. This is a volunteer project maintained
alongside other work, so please do not expect a same-day response. There is no bug bounty.

When reporting, the useful details are: what an attacker can do, what access they need to start
with, and which file or endpoint is involved.
