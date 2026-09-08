# Day 1 operations

Once the VM is bootstrapped, bring the cluster under Flux — see
[`bootstrap/README.md`](../bootstrap/README.md). Everything below is then
applied and reconciled automatically by Flux.

## Local path provisioner

Now managed by Flux — see
[`infrastructure/configs/base/local-path-provisioner/`](../infrastructure/configs/base/local-path-provisioner/).
The Talos-specific bits (host path `/var/mnt/local-path-provisioner`,
default StorageClass, `privileged` PodSecurity on the namespace) are
kustomize patches over the vendored upstream `v0.0.31` manifests.

> **NOTE**
> The VM must contain 2 disks: 1 for the system and 1 for the volume
> (nvme simulation if possible).

To validate after reconciliation:

```sh
kubectl get storageclass          # local-path is (default)
kubectl -n local-path-storage get pods
```

See the upstream [usage / validation](https://github.com/rancher/local-path-provisioner?tab=readme-ov-file#usage)
notes for a PVC + pod smoke test.
