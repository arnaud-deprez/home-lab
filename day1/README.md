# Day 1 operations

Once the VM is bootstraped, there are a couple of things to perform before we can use it.

## Setup a dynamic local path provisioner

Check [Talos doc](https://docs.siderolabs.com/kubernetes-guides/csi/local-storage#local-storage)

> **NOTE**
> To add a Local Path provisioner, the VM must contain 2 disks: 1 for the system and 1 for the volume (nvme simulation if possible)

```sh
pushd local-path-provisioner
kustomize build | kubectl apply -f -
popd
```
