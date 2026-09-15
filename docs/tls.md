# TLS / HTTPS on the laptop cluster

The `laptop` cluster issues certificates from an in-cluster private CA
(`ca-issuer`, backed by the `home-lab-ca` root in `infrastructure/configs/laptop/ca-issuer/`).
Apps get a trusted-looking padlock only after that root is imported as a
trusted CA on the device doing the browsing. This is a one-time step per
device — not automated.

Once Traefik's HTTP → HTTPS redirect reconciles, plain `http://` access
stops working for every app until the CA is trusted on each device —
including, if done via a browser, the very device being used to fetch the
CA cert (the `kubectl`-based export below is unaffected, since it doesn't
go through Traefik). Recommended order: reconcile, confirm the app's
`Certificate` is `Ready: True`, import the CA on the device, then verify
`https://<host>` shows a valid chain — before or alongside the redirect
taking effect.

### Export the root CA certificate

```sh
export KUBECONFIG="$PWD/os/context/kubeconfig"
kubectl get secret home-lab-ca -n cert-manager -o jsonpath='{.data.ca\.crt}' \
  | base64 -d > home-lab-ca.crt
```

### Trust it (macOS)

```sh
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain home-lab-ca.crt
```

Or via Keychain Access: File → Import Items → select `home-lab-ca.crt` →
double-click the imported cert → Trust → "When using this certificate:
Always Trust".

Firefox keeps its own trust store separate from the OS — import
`home-lab-ca.crt` under Settings → Privacy & Security → Certificates → View
Certificates → Authorities → Import, if you use Firefox against
`*.home.arpa` hosts.

### Rotation

The root (`home-lab-ca` Certificate, 10-year duration) auto-renews via
cert-manager well before expiry as long as its Secret survives (note:
renewal re-issues over the same key by default, so even a routine renewal
near the 10-year mark means re-importing the new certificate — this isn't
limited to the Secret-loss scenario below). If the Secret is ever lost
(e.g. PVC/etcd loss), cert-manager mints a **new** root the next time
`selfsigned-bootstrap` issues it — every device then needs the new
`home-lab-ca.crt` re-imported, and previously-trusted certs from the old
root stop being trusted.

### Trusting the CA *inside* the cluster

Pods only ever get the CA on `svc.cluster.local` reach for other pods; they
have no reason to trust `home-lab-ca` for outbound HTTPS by default. This
only matters when one app calls another app's `*.home.arpa` ingress
server-side (not from the browser) — e.g. Immich's OIDC discovery fetch to
`https://id.home.arpa` (see `apps/laptop/immich/ca-cert-configmap.yaml` and
the `NODE_EXTRA_CA_CERTS` env var in `apps/laptop/immich/helmrelease-patch.yaml`).
The pattern for any future app that needs this:

1. Copy the CA cert into that app's namespace as a plain (unencrypted —
   it's a public cert) `ConfigMap`.
2. Mount it into the container and point the language runtime's CA bundle
   env var at it (`NODE_EXTRA_CA_CERTS` for Node, `SSL_CERT_FILE` for Go,
   `REQUESTS_CA_BUNDLE` for Python, etc.) — or add it to the container's
   system trust store if there's no such env var.

There's no cluster-wide auto-distribution of this CA (e.g. `trust-manager`)
yet — each app that needs it gets its own copy, following Immich's example
above.

### Verifying a specific app

```sh
kubectl get certificate -n <namespace>
kubectl describe certificate <name> -n <namespace>   # Ready: True when issued
curl -v https://<host>                                 # valid chain once the root is trusted
curl -v http://<host>                                  # 301/308 redirect to https://
```
