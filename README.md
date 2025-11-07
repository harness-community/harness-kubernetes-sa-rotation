# harness-kubernetes-sa-rotation

drone plugin for creating kubernetes service account rotation with python

a new service account token will be created for the target service account in its namespace

if configured, existing k8s secrets will be deleted after the new service account token is stored in harness

<img width="1615" height="1156" alt="image" src="https://github.com/user-attachments/assets/0d59b045-6c97-4b93-a06f-b362d71a8941" />

## usage

- PLUGIN_K8S_NAMESPACE: k8s namespace (optional: default `harness-delegate-ng`)
- PLUGIN_K8S_SERVICE_ACCOUNT: k8s service account (optional: default `harness-delegate-ng`)
- PLUGIN_HARNESS_PLATFORM_API_KEY: harness platform api key
- PLUGIN_HARNESS_ACCOUNT: harness account id
- PLUGIN_HARNESS_ORG: harness org (optional: default `None`)
- PLUGIN_HARNESS_PROJECT: harness project (optional: default `None`)
- PLUGIN_SECRET_TAGS: secret tags (optional: default `{}`)
- PLUGIN_DELETE_K8S_SECRETS: delete existing k8s secrets (optional: default `False`)

there is an example service account, role, and binding in `service-account.yaml` to give least permissions to the plugin for execution

you can then run the plugin directly in harness using a container step in the target namespace using the service account

```yaml
- stepGroup:
    name: sg
    identifier: sg
    steps:
      - step:
          type: Plugin
          name: rotator
          identifier: rotator
          spec:
            connectorRef: account.harnessImage
            image: rssnyder/harness-kubernetes-sa-rotation
            settings:
              HARNESS_ACCOUNT: <+account.identifier>
              HARNESS_PLATFORM_API_KEY: <+secrets.getValue("account.account_admin")>
              HARNESS_ORG: <+org.identifier>
              HARNESS_PROJECT: <+project.identifier>
              SECRET_TAGS: "{\"source\":\"plugin\"}"
              DELETE_K8S_SECRETS: "true"
    stepGroupInfra:
      type: KubernetesDirect
      spec:
        connectorRef: remote_cluster
        namespace: harness-delegate-ng
        serviceAccountName: sa-token-rotator
```

you can use the cluster you are rotating the sa in as the stage infrastructure to run the plugin
