# Charmed Mongos K8s operator

[![Charmhub](https://charmhub.io/mongos-k8s/badge.svg)](https://charmhub.io/mongos-k8s)
[![Release to 6/edge](https://github.com/canonical/mongos-k8s-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/mongos-k8s-operator/actions/workflows/release.yaml)
[![Tests](https://github.com/canonical/mongos-k8s-operator/actions/workflows/ci.yaml/badge.svg)](https://github.com/canonical/mongos-k8s-operator/actions/workflows/ci.yaml)

## Overview

The Charmed Mongos K8s operator deploys and operates `mongos` instances on Kubernetes.

[`mongos`](https://www.mongodb.com/docs/v6.0/reference/program/mongos/) is a router for connecting client applications to a sharded MongoDB K8s clusters. It is the only way to access a sharded MongoDB cluster from the client perspective.

To deploy a sharded MongoDB cluster, see our [charmed solution for MongoDB K8s](https://charmhub.io/mongodb-k8s).

For information about how to deploy, integrate, and manage this charm, see the Official [Charmed Mongos documentation](https://charmhub.io/mongos-k8s).

## Get started
The following steps will guide you through briefly creating a client connection to a sharded MongoDB cluster via `mongos-k8s`. 

You'll need a Juju environment and a MongoDB K8s application deployed as a sharded cluster. For guidance about setting up your environment, see the [Charmed MongoDB K8s tutorial](https://charmhub.io/mongodb-k8s/docs/t-set-up).

### Deploy
To deploy a MongoDB K8s sharded cluster with one shard, run:
```
juju deploy mongodb-k8s --config role="config-server" config-server --trust
juju deploy mongodb-k8s --config role="shard" shard0 --trust
```
> The `--trust` flag is necessary to grant the application access to the Kubernetes cluster.

To deploy `mongos-k8s` and `data-integrator`, run:

```none
juju deploy mongos-k8s --trust
juju deploy data-integrator --config database-name=<name>
```

### Integrate 
When the status of the `mongos-k8s` application becomes `idle`, integrate `mongos-k8s` with `data-integrator` and with the `mongodb` application running as `config-server`:
```none
juju integrate mongos-k8s data-integrator
juju integrate config-server mongos-k8s
```

### Access the database
In order to access the integrated database, you will need the `mongos` URI. To retrieve this, run the following command:
```none
juju run data-integrator/leader get-credentials
```

You will find the URI under the field `uris` in the output.
<!--TODO: Uncomment when sharding tutorial is up.
> For more information about accessing the database, see the Charmed MongoDB documentation for [accessing a client database](https://charmhub.io/mongodb/docs/t-integrate-sharding#heading--access-integrated-database).
-->

### Enable TLS
If the sharded MongoDB cluster has TLS enabled, `mongos-k8s` must also enable TLS. Enable it by integrating `mongos-k8s` with a TLS application:
```none
juju integrate mongos-k8s <tls-application>
```
> For more information about TLS in sharded clusters, see the Charmed MongoDB documentation for [enabling security in sharded clusters](https://charmhub.io/mongodb/docs/t-enable-tls-sharding)

### External connections

If you would like to connect the sharded MongoDB K8s cluster *outside* of Juju, this is possible with the configuration `expose-external` in the mongos-k8s charm.

Simply configure the charm to use `nodeport`:
```shell
 juju config mongos-k8s expose-external=nodeport
```

This will make the mongos router accessible outside of Juju and will provide you access to the cluster. You will now see that all of your client URIs have been updated. 

For example run:
```
juju run data-integrator get-credentials
```

You will see that the URI has changed.

To reconfigure the charm to have internal access only, run:
```shell
 juju config mongos-k8s expose-external=none
```

### Remove `mongos`
To remove a `mongos` connection to the sharded cluster, run:
```none
juju remove-relation config-server mongos
```
When`mongos` is removed from the sharded cluster, the client is removed as well.

## Learn more
* Learn more about operating MongoDB sharded clusters and replica sets in the [Charmed MongoDB K8s documentation](https://charmhub.io/mongodb-k8s)
* Check the charm's [GitHub repository](https://github.com/canonical/mongos-k8s-operator)
* Learn more about the `mongos` router in the upstream [`mongos` documentation](https://www.mongodb.com/docs/v6.0/reference/program/mongos/)

## Project and community
Charmed Mongos K8s is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.

* Check our [Code of Conduct](https://ubuntu.com/community/ethos/code-of-conduct)
* Raise software issues or feature requests on [GitHub](https://github.com/canonical/mongos-k8s-operator/issues)
* Report security issues through [LaunchPad](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File). 
* Meet the community and chat with us on [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com)
* [Contribute](https://github.com/canonical/mongodb-k8s-operator/blob/main/CONTRIBUTING.md) to the code
