# Contributing

## Overview

This documents explains the processes and practices recommended for contributing enhancements to
this operator.

- Generally, before developing enhancements to this charm, you should consider
  [opening an issue
  ](https://github.com/canonical/mongos-k8s-operator/issues) explaining
  your use case.
- If you would like to chat with us about your use-cases or proposed
  implementation, you can reach us at [Canonical Mattermost public
  channel](https://chat.charmhub.io/charmhub/channels/charm-dev) or
  [Discourse](https://discourse.charmhub.io/).
- Familiarising yourself with the [Charmed Operator
  Framework](https://juju.is/docs/sdk) library will help you a lot when working
  on new features or bug fixes.
- All enhancements require review before being merged. Code review typically
  examines
  - code quality
  - test coverage
  - user experience for Juju administrators this charm.
- Please help us out in ensuring easy to review branches by rebasing your pull
  request branch onto the `main` branch. This also avoids merge commits and
  creates a linear Git commit history.

## Developing
Install `tox`, `poetry`, and `charmcraftcache`

Install pipx: https://pipx.pypa.io/stable/installation/
```shell
pipx install tox
pipx install poetry
pipx install charmcraftcache
```

You can create an environment for development:

```shell
poetry install
```

### Testing

```shell
tox run -e format        # update your code according to linting rules
tox run -e lint          # code style
tox run -e unit          # unit tests
tox run -e integration   # integration tests
tox                      # runs 'lint' and 'unit' environments
```

## Build charm

Build the charm in this git repository using:

```shell
tox run -e build-dev
```

### Deploy

```bash
# Create a model
juju add-model dev
# Enable DEBUG logging
juju model-config logging-config="<root>=INFO;unit=DEBUG"
# Deploy the charm
# `--trust` needed if Role Based Access Control (RBAC) (https://kubernetes.io/docs/concepts/security/rbac-good-practices/) is enabled on Kubernetes
juju deploy ./mongos-k8s-operator_ubuntu-20.04-amd64.charm \
    --resource ghcr.io/canonical/charmed-mongodb:6.0.6-22.04_edge@sha256:b4b3edb805b20de471da57802643bfadbf979f112d738bc540ab148d145ddcfe --trust
```

## Canonical Contributor Agreement

Canonical welcomes contributions to the Charmed MySQL-Router Operator. Please
check out our [contributor agreement](https://ubuntu.com/legal/contributors) if
you're interested in contributing to the solution.