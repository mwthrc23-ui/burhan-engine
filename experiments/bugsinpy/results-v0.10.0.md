# BugsInPy results for Burhan 0.10.0

- Dataset: `soarsmu/BugsInPy@11c5f1eea954a42132cfd06bf257766a7963e0fd`
- External subject: `ytdl-org/youtube-dl` at four pinned buggy commits
- Runtime: `python@sha256:b53f496ca43e5af6994f8e316cf03af31050bf7944e0e4a308ad86c001cf028b`
- Total cases: 4
- Declared tests already passing before patch: 3
- False positives: 0/3 (0%)
- Repair-eligible cases: 1
- Patch success rate: 1/1 (100%), verified V2 fail-to-pass

For `youtube-dl-2`, the runner pins the regression test from the upstream fixed
commit by SHA-256, installs it on the buggy commit, observes the declared test
fail, and verifies the official BugsInPy patch as V2 fail-to-pass. The other
three declared tests already passed and were correctly rejected as negative
controls. The result does not claim accuracy for BugsInPy as a whole. See
`results-v0.10.0.json` for the machine-readable output and `README.md` for the
exact reproduction procedure.
