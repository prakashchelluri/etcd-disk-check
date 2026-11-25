# etcd-disk-check

Tells you whether a node's disk is fast enough for etcd, and explains why not.

> Lab project: a clean-room reference implementation of a pattern I use at work. It contains no employer code or data.

## The problem

etcd writes every change to its write-ahead log (WAL) and calls `fdatasync` before it
acknowledges the write. If the disk is slow to sync, everything above it gets slow: heartbeats
miss, leaders change, the Kubernetes API server logs `etcdserver: request timed out`, and
`kubectl` hangs. The symptoms look like a network or etcd problem, but the cause is often the
disk.

I have seen this on non-production Kubernetes clusters whose etcd ran on storage with low IOPS
limits. Moving etcd from HDD to SSD helped, but the virtual disks still could not deliver enough
I/O, and other writers on the node (container logs in particular) made it worse. The first step
in each case was the same: measure the disk the way etcd uses it, and read etcd's own latency
histograms. This tool does both and prints a verdict you can act on.

## Quick start

Python 3.11+, no runtime dependencies. `fio` is needed only for `bench`.

```sh
python -m pip install .

# 1. Benchmark the disk that holds (or will hold) etcd's data directory
sudo dnf install fio            # or: sudo apt-get install fio
etcd-disk-check bench --dir /var/lib/etcd

# 2. Read the latency etcd itself has observed
etcd-disk-check metrics http://127.0.0.1:2381/metrics      # kubeadm's plain-HTTP metrics port
etcd-disk-check metrics saved-metrics.txt
curl -s --cacert ca.crt --cert client.crt --key client.key https://127.0.0.1:2379/metrics \
  | etcd-disk-check metrics -

# Machine-readable output for cron or CI
etcd-disk-check metrics http://127.0.0.1:2381/metrics --json
```

Exit codes: `0` PASS, `1` WARN, `2` FAIL, `3` could not measure (fio missing, unreadable
input, usage error).

## Example output

Real runs on my workstation against the test fixtures in `tests/fixtures/`. The metrics fixtures
are hand-made histograms with etcd's bucket layout. `fio` is not available on this Windows
machine, so the `bench` example parses a fixture shaped like fio 3.x JSON output; the numbers
are illustrative, not a measurement of a real disk. CI runs the real benchmark on a Linux runner.

```text
$ etcd-disk-check metrics tests/fixtures/metrics_slow.prom
etcd-disk-check metrics: tests/fixtures/metrics_slow.prom

  FAIL  WAL fsync p99                 64.00 ms   (target < 10 ms)
  FAIL  backend commit p99           118.86 ms   (target < 25 ms)
        note                       percentiles are over the etcd process lifetime (cumulative counters)

Overall: FAIL

Why it matters:
  - WAL fsync p99: every etcd write waits for a WAL fsync; slow fsync delays writes and heartbeats and causes leader elections and API server timeouts.
  - backend commit p99: slow commits of the bbolt backend stall applying writes to the database and slow down compaction and snapshots.

Recommendations:
  - Put the etcd data directory (usually /var/lib/etcd) on a dedicated SSD or NVMe device.
  - Keep noisy neighbours off the etcd disk: no container images, container logs or application data on the same device.
  - Reduce node log write I/O, for example move container logs (/var/log/pods) to RAM-backed storage such as zram, with strict log rotation.
  - On VMs and cloud volumes, check the virtual disk's provisioned IOPS/throughput limits and whether the volume is being throttled.
```

```text
$ etcd-disk-check bench --fio-json tests/fixtures/fio_fast.json
etcd-disk-check bench: tests/fixtures/fio_fast.json

  PASS  fdatasync p99 latency          1.43 ms   (target < 10 ms)
        write IOPS                 812

Overall: PASS
```

```text
$ etcd-disk-check bench --dir .
etcd-disk-check: error: fio not found in PATH; install it first (e.g. 'dnf install fio' or 'apt-get install fio'), or analyse saved output with --fio-json
$ echo $?
3
```

## How it works

**bench** runs fio with the workload the etcd documentation suggests for checking a disk:
sequential 2300-byte writes with `fdatasync` after each one, which is close to how etcd appends
to its WAL.

```sh
fio --rw=write --ioengine=sync --fdatasync=1 --directory=<dir>/etcd-disk-check-XXXX \
    --size=22m --bs=2300 --name=etcd-check --output-format=json
```

The test file goes into a temporary sub-directory of `--dir`, so it is written to the disk you
are testing and removed afterwards. From the JSON it reads the 99th percentile of the sync
latency (`jobs[0].sync.lat_ns.percentile["99.000000"]`) and the write IOPS. `--fio-json` analyses
output saved from an earlier run instead, for example one taken on a node without Python.

**metrics** reads Prometheus text format from a file, an http(s) URL or stdin, collects the
`_bucket` series of `etcd_disk_wal_fsync_duration_seconds` and
`etcd_disk_backend_commit_duration_seconds` (summed across label sets, like `sum by (le)`), and
computes the 99th percentile the same way PromQL's `histogram_quantile()` does: find the bucket
that contains the rank and interpolate linearly inside it. If the rank lands in the `+Inf`
bucket, the highest finite bound is returned, as Prometheus does.

**Grading.** The targets are the commonly cited ones: p99 WAL fsync (and fio fdatasync) under
10 ms, p99 backend commit under 25 ms. A value at or above the target is FAIL. A value at or
above 80% of the target is WARN: it passes today but leaves little headroom. Anything that is not
PASS comes with an explanation of what the latency breaks and the list of fixes to try.

## Limitations and trade-offs

- **metrics mode looks at lifetime counters.** etcd's histograms are cumulative since the process
  started, so the p99 covers the whole uptime. A bad hour last week still counts, and a disk that
  just became slow is diluted. For a recent window, use PromQL in Prometheus:
  `histogram_quantile(0.99, sum by (le) (rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])))`.
- **Bucket resolution.** etcd's buckets double in size (1, 2, 4, 8, 16 ms ...), so an
  interpolated p99 is an estimate within its bucket, as it is in Prometheus.
- **fio is a model of etcd, not etcd.** It measures one writer on an idle-ish disk. A disk that
  passes on a quiet node can still fail under real load from noisy neighbours; that is what
  metrics mode is for.
- **The thresholds are guidance.** They are the widely quoted etcd numbers, not hard limits. The
  80% WARN band is my own choice.
- **No TLS client options.** For an etcd endpoint that needs client certificates, fetch with
  `curl` (as in the quick start) and pipe into `metrics -`.
- **The benchmark writes 22 MiB** to the target disk. Running it on a live etcd node adds load
  for the few seconds it takes; prefer a quiet moment.
- `bench` needs fio 3.5 or newer (older versions do not report sync latency percentiles in JSON).

## Development

```sh
python -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
ruff check . && ruff format --check . && python -m pytest -q
```

The tests run offline and do not need fio.

## License

MIT
