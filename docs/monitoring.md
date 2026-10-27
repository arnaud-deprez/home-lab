# Monitoring and alerting

What watches the clusters, where alerts go, how to test every path, and how to enable the same on another
cluster. Backups and their restore procedures are in [`backup-restore.md`](backup-restore.md); the
EuroNodes cluster itself in [`euronodes.md`](euronodes.md).

**Status:** enabled on **euronodes** only. The laptop and tierhive clusters have none of it yet
(see [Enabling on another cluster](#enabling-on-another-cluster)).

## 1. Overview

Four independent layers, so that no single failure silences everything:

| Layer | Runs | Watches | Reports to |
|---|---|---|---|
| **Flux notifications** | In the cluster (notification-controller) | Flux reconcile errors: a broken commit, a failing HelmRelease | Telegram |
| **Prometheus + Alertmanager** | In the cluster (`monitoring` namespace) | The node (memory, disk), pods (restarts, OOM, Pending, replicas), certificates, PostgreSQL | Telegram |
| **healthchecks.io pings** | CronJobs and Alertmanager in the cluster, checks hosted by healthchecks.io | Backups, Storage Box space, and Prometheus itself (Watchdog): it alerts when a ping is **missing**, so it also catches a dead cluster | Telegram (+ email) |
| **External probe** | GitHub Actions, outside the cluster | The public URLs and their TLS certificates, as a user sees them | Telegram |

Rule of thumb: Flux and Prometheus tell you what is wrong *inside*; healthchecks.io and the probe tell you
when the inside cannot speak at all.

## 2. Where alerts go

One Telegram channel, **Powple Homelab alerts**, for every cluster (each message names its cluster). Two bots
post to it (both are administrators of the channel):

| Bot | Used by | Token stored in |
|---|---|---|
| Cluster bot | Flux notifications and Alertmanager | SOPS secrets in this repo (encrypted with the cluster's age key) |
| Probe bot | The GitHub Actions probe | The GitHub Actions secret `PROBE_TELEGRAM_BOT_TOKEN` |

They are separate so that a leaked probe token (it lives at GitHub) can be revoked in BotFather without
silencing the cluster's own alerts. Healthchecks.io has its own Telegram integration (its bot, configured in
its web UI) and also emails the account address by default.

The channel ID (`-1004308064066`) is not a secret and sits in plain text in the manifests and the workflow.

## 3. What alerts

### In-cluster (Prometheus rules, shared by every cluster that enables monitoring)

Defined in [`infrastructure/controllers/base/monitoring/helmrelease.yaml`](../infrastructure/controllers/base/monitoring/helmrelease.yaml).

| Alert | Fires when | Usual cause |
|---|---|---|
| `NodeMemoryHigh` | Memory above 90% for 10 min | A leak, or too much running on the 8 GiB VM |
| `NodeDiskFilling` | A filesystem under 20% free for 10 min (the rclone S3 mount is excluded) | Images, logs, PVC growth, the Prometheus volume |
| `TargetDown` | Any scrape target down for 10 min | A pod crashed or is being replaced |
| `PodCrashLooping` | 3 or more container restarts in 30 min | A bad config or image, a failing dependency |
| `ContainerOOMKilled` | A container was OOM-killed | Its memory limit is too low |
| `PodNotRunning` | Pending or Unknown for 15 min | Scheduling or image pull problems (see the `registry.k8s.io` note below) |
| `DeploymentUnavailable` | Fewer available replicas than desired for 15 min | As above |
| `CertificateExpiringSoon` | A cert-manager certificate expires in under 14 days | Renewal is failing (port 80, Traefik, Let's Encrypt) |
| `CertificateNotReady` | A certificate is not Ready for 15 min | As above |
| `PostgresDown` | A CloudNativePG instance does not answer for 5 min | The database pod or its disk |
| `Watchdog` | **Always** (not an error): it is the heartbeat, routed to healthchecks.io only | see below |

Alertmanager groups by alert name and cluster, waits 30 s before the first message, and repeats a still-firing
alert every 12 h. Resolved alerts are announced.

### Flux

An `Alert` named `flux-errors` sends **error** events (not info) from Flux itself and from the HelmReleases of
the infrastructure namespaces plus the cluster's app namespaces. Source:
[`infrastructure/configs/base/flux-notifications/`](../infrastructure/configs/base/flux-notifications/).
An Alert can only watch **one namespace per source** (there is no all-namespaces wildcard), so a cluster
overlay lists the namespaces of its own apps (euronodes adds `immich`; Pocket ID is a plain Deployment with no
HelmRelease).

### healthchecks.io checks (euronodes)

| Check | Pinged by | Schedule (UTC) | Grace | Alerts when |
|---|---|---|---|---|
| `euronodes-immich-backup` | The Immich restic job | cron `0 4 * * *` | 14 h | The run fails, or no ping arrives |
| `euronodes-pocket-id-backup` | The Pocket ID restic job | cron `30 3 * * *` | 2 h | Same |
| `euronodes-storagebox-usage` | The `storagebox-usage` CronJob | cron `0 6 * * *` | 6 h | The Storage Box is 80% full or more, the box cannot be queried, or no ping arrives |
| `euronodes-monitoring-watchdog` | Alertmanager (the `Watchdog` alert), about every 5 min | period 10 min | 10 min | The pings stop: Prometheus, Alertmanager or the cluster itself is down |

The backup jobs send `/start`, then their exit code. A missing ping is itself the alert, which covers a job that
never started (failed init container, suspended CronJob, dead VM). The ping URLs are SOPS secrets (key
`HC_PING_URL`, see section 5); a job without a URL runs unchanged and simply reports nothing.

### External probe (GitHub Actions)

[`.github/workflows/probe.yaml`](../.github/workflows/probe.yaml) runs
[`.github/scripts/probe.sh`](../.github/scripts/probe.sh) at minutes 7 and 37 of every hour. For each cluster in
its matrix it checks that each URL answers with the expected status and that each host's TLS certificate has
more than 14 days left; failures go to Telegram with a link to the run.

| Cluster | Checks |
|---|---|
| euronodes | `https://id.vps.powple.com/healthz` (204), `https://immich.vps.powple.com/api/server/ping` (200) |

- It alerts on **every failing run** (every 30 min during an outage) and is silent otherwise: it keeps no state
  and sends no recovery message. The run turns red in GitHub, which can also email you.
- Transient errors are retried before an alert.
- **Cost:** each run is billed at least one Actions minute. One cluster is about 1,440 minutes a month, and each
  cluster added to the matrix multiplies that. A private repo has a monthly quota; check Settings, Billing.
- Scheduled workflows only run from the **default branch**, and GitHub may delay them by several minutes.
- The checks differ per cluster on purpose (public URLs, health paths), so each cluster has its own matrix entry.

## 4. Prometheus

Components, from the `prometheus` chart (version in the HelmRelease): Prometheus server, Alertmanager,
kube-state-metrics and node-exporter. No Grafana, no pushgateway. Data: 15 days or 4 GB, on a 5 Gi volume.
Scrape jobs: Prometheus itself, kubelet and cAdvisor, pods and services that advertise `prometheus.io/*`
annotations (cert-manager, Traefik, kube-state-metrics, node-exporter) and CloudNativePG (operator and every
instance, matched by the port named `metrics`).

**The UI is reachable only over Tailscale**, never through Traefik: an Ingress of class `tailscale` makes the
operator publish it on the tailnet. On euronodes: `https://prometheus-euronodes.tailb63fde.ts.net`.

- Prometheus has **no authentication**: the tailnet policy is the access control. Your identity needs a grant
  to the proxy's tag (`tag:k8s` by default, owned by `tag:k8s-operator`) on `tcp:443`, as for the other
  operator-exposed services in [`tailscale.md`](tailscale.md). Anything else carrying that tag becomes
  reachable by the same grant.
- Without the tailnet: `kubectl -n monitoring port-forward svc/prometheus-server 9090:80`.
- Useful pages: **Alerts** (what fires or is pending), **Status, Targets** (is every scrape target up), and the
  query box, for example `100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)`.

Measured on 2026-10-27, right after the first deploy: node memory 49% of 7.7 GiB, Prometheus about 120 MiB.

## 5. Secrets

All SOPS-encrypted with the cluster's age key; edit them with `sops <file>`, and never paste a token or a ping
URL into a chat or a commit message.

| File | Key | What |
|---|---|---|
| `infrastructure/configs/<cluster>/telegram-token.sops.yaml` | `token` | Cluster bot token, for the Flux `Provider` (namespace `flux-system`) |
| `infrastructure/controllers/<cluster>/alertmanager-telegram.sops.yaml` | `token` | Cluster bot token, for Alertmanager (namespace `monitoring`) |
| `infrastructure/controllers/<cluster>/alertmanager-watchdog.sops.yaml` | `url` | Ping URL of the `euronodes-monitoring-watchdog` check |
| `apps/<cluster>/immich/restic-secret.sops.yaml` | `HC_PING_URL` | Ping URL of the Immich backup check |
| `apps/<cluster>/pocket-id/restic-secret.sops.yaml` | `HC_PING_URL` | Ping URL of the Pocket ID backup check |
| `apps/<cluster>/immich/storagebox-usage-secret.sops.yaml` | `HC_PING_URL` | Ping URL of the Storage Box usage check |
| GitHub Actions secret `PROBE_TELEGRAM_BOT_TOKEN` | | Probe bot token (`gh secret set PROBE_TELEGRAM_BOT_TOKEN`) |

The cluster bot's token appears in two secrets because a Secret can only be read from its own namespace.
Rotating it means `/revoke` in BotFather, then `sops` on both files.

## 6. Testing each path

Run these after changing anything here, and now and then to prove the chain still works.

```sh
export KUBECONFIG=$PWD/talos/euronodes/kubeconfig     # reachable over Tailscale

# Flux notification: a Kustomization with a bad path raises an error event; delete it afterwards
kubectl apply -f - <<'EOF'
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata: {name: notify-test, namespace: flux-system}
spec: {interval: 1m, path: ./does-not-exist, prune: false, sourceRef: {kind: GitRepository, name: flux-system}}
EOF
kubectl -n flux-system delete kustomization notify-test

# Alertmanager to Telegram: post a synthetic alert (it expires after 3 minutes; `date -v` is macOS syntax)
kubectl -n monitoring port-forward prometheus-alertmanager-0 9093:9093 &
curl -s -X POST localhost:9093/api/v2/alerts -H 'Content-Type: application/json' -d \
  "[{\"labels\":{\"alertname\":\"TestAlert\",\"cluster\":\"euronodes\"},\"annotations\":{\"summary\":\"test\"},\"endsAt\":\"$(date -u -v+3M +%Y-%m-%dT%H:%M:%SZ)\"}]"

# Backup and Storage Box pings: run the jobs by hand, then look at the check in healthchecks.io
kubectl -n identity create job --from=cronjob/pocket-id-backup manual-1
kubectl -n immich create job --from=cronjob/storagebox-usage manual-1
kubectl -n immich logs job/manual-1                    # for the Storage Box job: prints the usage line
```

- **Failure path of a healthchecks.io check:** in the check's page use the failing-ping option, or send
  `/fail` to its ping URL, and confirm Telegram. The Storage Box threshold can be tested by lowering
  `THRESHOLD_PERCENT` in the CronJob.
- **External probe:** run the workflow from the Actions tab (**Run workflow**), or locally with
  `DRY_RUN=1 CLUSTER=euronodes CHECKS='name|https://host/path|200' .github/scripts/probe.sh`.
- Delete the manual jobs when done (`kubectl delete job manual-1 -n <namespace>`).

## 7. Known limits and gaps

- **EuroNodes cannot pull from `registry.k8s.io`** (Google's registry answers 403 to this VPS's IP range).
  kube-state-metrics is published only there, so the euronodes overlay pulls it from Rancher's Docker Hub
  mirror (`rancher/mirrored-kube-state-metrics-kube-state-metrics`, v2.17.0, older than the chart's default).
  Revisit it when upgrading the chart; other clusters use the upstream image.
- **S3 bucket usage is not monitored.** EuroNodes' S3 API gives no free-space figure; the VM disks are watched,
  the Storage Box is, but not how full the bucket is. Immich's own statistics are the only view.
- **No dashboards** (no Grafana), and no log collection.
- **Backups are checked only by their pings and `restic check`** (which reads 5% of the data). Test a restore
  now and then (see `backup-restore.md`).
- **The probe repeats every 30 minutes during an outage** and sends no recovery message (see section 3).
- **If a ping URL is missing from a secret**, the matching job still runs but reports nothing and its check
  never gets a ping: it goes red after the grace time, which is the intended failure mode.
- **A broken Alertmanager bot token is not alerted.** The Watchdog travels to healthchecks.io, not Telegram, so
  an expired or revoked token only shows in the logs (`kubectl -n monitoring logs prometheus-alertmanager-0
  -c alertmanager`). Run the Alertmanager test in section 6 after rotating a token.

## Enabling on another cluster

Monitoring is a shared base that a cluster opts into; nothing runs on a cluster that does not list it. For a
cluster `<cluster>` (laptop or tierhive; copy the euronodes files as a template):

1. **Secrets** (encrypted with `<cluster>`'s age key, picked by path from `.sops.yaml`): create
   `telegram-token.sops.yaml` in `infrastructure/configs/<cluster>/`, `alertmanager-telegram.sops.yaml` and
   `alertmanager-watchdog.sops.yaml` in `infrastructure/controllers/<cluster>/`. Copy the euronodes files and
   `sops -e -i` them, then `sops` to put real values in. Create a healthchecks.io check for the Watchdog
   (period 10 min, grace 10 min).
2. **Controllers overlay** (`infrastructure/controllers/<cluster>/kustomization.yaml`): add `../base/monitoring`
   and the two Alertmanager secrets to `resources`, and copy the three patches from euronodes:
   `monitoring-patch.yaml` (the `cluster` label), `monitoring-ingress-patch.yaml` (a **unique** tailnet hostname,
   for example `prometheus-<cluster>`) and the JSON patch that sets `chat_id`.
3. **Configs overlay** (`infrastructure/configs/<cluster>/kustomization.yaml`): add `../base/flux-notifications`
   and `telegram-token.sops.yaml`, copy `flux-notifications-patch.yaml` (chat ID, `cluster` name) and the JSON
   patch that appends the namespaces of the cluster's own HelmReleases.
4. **Cluster specifics.** Only if the cluster's IP range cannot reach `registry.k8s.io`, override the
   kube-state-metrics image as euronodes does. Alert rules are shared; add any cluster-specific rule through a
   patch in the overlay, not in the base. Size the Prometheus volume and limits to the node.
5. **Probe and backup checks are per cluster.** Add a matrix entry in `probe.yaml` only for a cluster with a
   public endpoint (mind the Actions minutes), and add checks for its own backup stack (for example the
   Storage Box CronJob exists only on euronodes, where restic writes to a Storage Box).
6. Validate with the `flux-ops` workflow (render, dry-run, `flux diff`), then test every path in section 6.
