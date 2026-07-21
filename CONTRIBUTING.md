# Contributing

## Overview

This document explains the processes and practices recommended for contributing enhancements to this operator.

**Note:** The charm's python business logic is written in a shared library that can be found [here](https://github.com/canonical/mongo-single-kernel-library). This is where python contributions should be made.

- Generally, before developing enhancements to this charm, you should consider opening an issue [on Single Kernel repository](https://github.com/canonical/mongo-single-kernel-library/issues) explaining your use case.
- If you would like to chat with us about your use-cases or proposed implementation, you can reach us on our [Matrix channel](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) or in [Discourse](https://discourse.charmhub.io/).
- Familiarising yourself with the [Charmed Operator Framework](https://juju.is/docs/sdk) library will help you a lot when working on new features or bug fixes.
- All enhancements require review before being merged. Additionally, new code must pass the tests. Code review typically examines
  - code quality
  - test coverage
  - user experience for Juju administrators of this charm.
- Please help us out in ensuring easy to review branches by rebasing your pull request branch onto the `main` branch. This also avoids merge commits and creates a linear Git commit history.
- Once the code has been merged on the [repository](https://github.com/canonical/mongo-single-kernel-library/) of the Mongo Single Kernel lib, wait for a new version of the [python package](https://pypi.org/project/mongo-charms-single-kernel/) to be published, and create a PR on this repository that bumps the version of the package, and on the 3 other repositories ([MongoDB k8s](https://github.com/canonical/mongodb-k8s-operator), [MongoDB VM](https://github.com/canonical/mongodb-operator) and [Mongos k8s](https://github.com/canonical/mongos-k8s-operator)).
- If you added some new interfaces, please don't forget to add them here.

## Developing

Install `tox`, `poetry`, and `charmcraftcache`

Install [pipx](https://pipx.pypa.io/stable/installation/)

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
tox run -e format                          # update your code according to linting rules
tox run -e lint                            # code style
tox run -e integration                     # integration tests
tox run -e terraform-lint                  # Terraform format validation
tox run -e terraform-tests -- <model-uuid> # Terraform sanity tests
tox                                        # runs 'lint' environment
```

### Terraform tests

The Terraform test module expects an existing Juju model and takes the model UUID as input. This
is a primitive smoke test: it only deploys Mongos with `data-integrator` and performs no behavioral
checks.

```shell
juju add-model test-mongos-tf
model_uuid="$(juju show-model test-mongos-tf | awk -F': ' '/model-uuid/ {print $2}')"
tox run -e terraform-test -- "${model_uuid}"
```

After testing, clean up the Terraform resources and Juju model:

```shell
cd terraform/tests
terraform destroy -var "model_uuid=${model_uuid}" -auto-approve
cd -
juju destroy-model test-mongos-tf --destroy-storage --force --no-prompt
```

## Build charm

Build the charm in this git repository using:

```shell
charmcraft pack
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
    --resource mongodb-image=ghcr.io/canonical/charmed-mongodb@sha256:7ddb80a3b5ddffa95704a8980fc11037ba1a23273a9805214bc42be9f507107f --trust
```

## Canonical Contributor Agreement

Canonical welcomes contributions to the Charmed MySQL-Router Operator. Please
check out our [contributor agreement](https://ubuntu.com/legal/contributors) if
you're interested in contributing to the solution.
