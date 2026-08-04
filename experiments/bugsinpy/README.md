# BugsInPy external-patch experiment

This experiment is an external negative-control study, not a benchmark written
for Burhan. It pins BugsInPy at commit
`11c5f1eea954a42132cfd06bf257766a7963e0fd`, selects four declared
`youtube-dl` bug tests, validates every dataset file by SHA-256, and runs the
structured unittest arguments in a network-disabled Python 3.7 Docker image
pinned by digest.

The runner never executes `run_test.sh`. It compares that file with the pinned
declaration, then passes an explicit argument list to `ProofRunner` with
`shell=False`. The official patch is verified only when the declared test first
fails and the same test passes after the patch. A test that already passes is a
negative control and the patch must be rejected.

## Reproduce

```bash
git clone https://github.com/soarsmu/BugsInPy.git /tmp/BugsInPy
git -C /tmp/BugsInPy checkout --detach 11c5f1eea954a42132cfd06bf257766a7963e0fd
git clone https://github.com/ytdl-org/youtube-dl.git /tmp/youtube-dl
docker pull python@sha256:b53f496ca43e5af6994f8e316cf03af31050bf7944e0e4a308ad86c001cf028b
PYTHONPATH=src python scripts/run-bugsinpy-experiment.py \
  --manifest experiments/bugsinpy/manifest.json \
  --dataset-root /tmp/BugsInPy \
  --subject-repository /tmp/youtube-dl \
  --output /tmp/burhan-bugsinpy-results-v0.10.0.json
diff -u experiments/bugsinpy/results-v0.10.0.json \
  /tmp/burhan-bugsinpy-results-v0.10.0.json
```

The checked-in result is deliberately honest. `youtube-dl-2` uses the upstream
regression test from its pinned fixed commit (also pinned by SHA-256): it fails
on the buggy commit and the official BugsInPy patch passes under V2. The other
three declared tests already pass, so Burhan rejects their patches before
application. The result is 1/1 external patch success and 0/3 false positives.
