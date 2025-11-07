from os import getenv
from sys import exit
from typing import Optional
import time
import logging
import json

from requests import post, put
from kubernetes import client, config
from kubernetes.client.rest import ApiException


def write_outputs(outputs: dict[str, str]):
    """
    write key value outputs to a local file to be rendered in the plugin step

    args:
        outputs (dict[str, str]): string to string mappings
    """

    output_file = open(getenv("DRONE_OUTPUT", "DRONE_OUTPUT.env"), "w")

    for k, v in outputs.items():
        output_file.write(f"{k}={v}\n")

    output_file.close()


def write_secret_outputs(outputs: dict[str, str]):
    """
    write key value outputs to a local file to be rendered in the plugin step as secret

    args:
        outputs (dict[str, str]): string to string mappings
    """

    output_file = open(
        getenv("HARNESS_OUTPUT_SECRET_FILE", "HARNESS_OUTPUT_SECRET.env"), "w"
    )

    for k, v in outputs.items():
        output_file.write(f"{k}={v}\n")

    output_file.close()


def check_env(variable: str, default: str = None):
    """
    resolves an environment variable, returning a default if not found
    if no default is given, variable is considered required and must be set
    if not, print the required var and fail the program

    args:
        variable (str): environment variable to resolve
        default (str): default value for variable if not found

    returns:
        str: the value of the variable
    """

    value = getenv(variable, default)
    if value == None:
        # if we are missing a PLUGIN_ var, ask the user for the expected setting
        stripped_variable = variable if "PLUGIN_" not in variable else variable[7:]
        print(f"{stripped_variable} required")
        exit(1)

    return value


def load_k8s_config(kubeconfig_path: Optional[str] = None):
    """
    Load Kubernetes configuration from kubeconfig file or in-cluster config.

    args:
        kubeconfig_path (str, optional): Path to kubeconfig file. If None, uses in-cluster config or default kubeconfig
    """
    if kubeconfig_path:
        config.load_kube_config(config_file=kubeconfig_path)
    else:
        try:
            # Try in-cluster config first (when running inside a pod)
            config.load_incluster_config()
        except config.ConfigException:
            # Fall back to default kubeconfig
            config.load_kube_config()


def get_k8s_secret(
    namespace: str, name: str, kubeconfig_path: Optional[str] = None
) -> dict:
    """
    Resolve the value of a kubernetes secret

    args:
        namespace (str): Kubernetes namespace where the secret exists
        name (str): Name of the secret to retrieve
        kubeconfig_path (str, optional): Path to kubeconfig file

    returns:
        dict: Dictionary containing the secret data (decoded from base64)
    """
    import base64

    try:
        load_k8s_config(kubeconfig_path)
        v1 = client.CoreV1Api()

        # Retrieve the secret
        secret = v1.read_namespaced_secret(name=name, namespace=namespace)

        # Decode secret data from base64
        secret_data = {}
        if secret.data:
            for key, value in secret.data.items():
                secret_data[key] = base64.b64decode(value).decode("utf-8")

        return secret_data

    except ApiException as e:
        print(f"Error retrieving secret '{name}' from namespace '{namespace}': {e}")
        raise


def create_service_account_token(
    namespace: str,
    service_account: str,
    token_name: str,
    labels: dict[str, str] = {},
    kubeconfig_path: Optional[str] = None,
) -> str:
    """
    Create a kubernetes service account token

    args:
        namespace (str): Kubernetes namespace where the service account exists
        service_account (str): Name of the service account
        token_name (str): Name for the token secret
        kubeconfig_path (str, optional): Path to kubeconfig file

    returns:
        str: The generated token
    """
    import base64

    try:
        load_k8s_config(kubeconfig_path)
        v1 = client.CoreV1Api()

        # Create a secret of type kubernetes.io/service-account-token
        secret = client.V1Secret(
            api_version="v1",
            kind="Secret",
            metadata=client.V1ObjectMeta(
                name=token_name,
                annotations={"kubernetes.io/service-account.name": service_account},
                labels=labels,
            ),
            type="kubernetes.io/service-account-token",
        )

        # Try to create the secret
        try:
            v1.create_namespaced_secret(namespace=namespace, body=secret)
            print(f"Service account token '{token_name}' created successfully")
        except ApiException as e:
            if e.status == 409:
                # Secret already exists, retrieve it
                print(
                    f"Service account token '{token_name}' already exists, retrieving..."
                )
                v1.read_namespaced_secret(name=token_name, namespace=namespace)
            else:
                raise

        # Wait for the token to be populated (it may take a moment)
        max_retries = 10
        for i in range(max_retries):
            secret_obj = v1.read_namespaced_secret(name=token_name, namespace=namespace)
            if secret_obj.data and "token" in secret_obj.data:
                token = base64.b64decode(secret_obj.data["token"]).decode("utf-8")
                return token
            time.sleep(1)

        raise Exception(f"Token not populated after {max_retries} seconds")

    except ApiException as e:
        print(f"Error creating service account token '{token_name}': {e}")
        raise


def list_k8s_secrets(
    namespace: str, search_string: str = "", kubeconfig_path: Optional[str] = None
) -> list[dict]:
    """
    Retrieve a list of Kubernetes secrets in a namespace that match a search string.

    args:
        namespace (str): Kubernetes namespace to search in
        search_string (str): String to search for in secret names (case-insensitive). Empty string returns all secrets.
        kubeconfig_path (str, optional): Path to kubeconfig file

    returns:
        list[dict]: List of dictionaries containing secret metadata (name, type, creation_timestamp, data_keys)
    """
    try:
        load_k8s_config(kubeconfig_path)
        v1 = client.CoreV1Api()

        # List all secrets in the namespace
        secrets = v1.list_namespaced_secret(namespace=namespace)

        # Filter secrets by search string
        matching_secrets = []
        search_lower = search_string.lower()

        for secret in secrets.items:
            secret_name = secret.metadata.name

            # If search string is empty or matches the secret name
            if not search_string or search_lower in secret_name.lower():
                secret_info = {
                    "name": secret_name,
                    "type": secret.type,
                    "creation_timestamp": secret.metadata.creation_timestamp.isoformat()
                    if secret.metadata.creation_timestamp
                    else None,
                    "data_keys": list(secret.data.keys()) if secret.data else [],
                }
                matching_secrets.append(secret_info)

        return matching_secrets

    except ApiException as e:
        print(f"Error listing secrets in namespace '{namespace}': {e}")
        raise


def delete_k8s_secret(
    namespace: str, secret_name: str, kubeconfig_path: Optional[str] = None
):
    """
    Delete a kubernetes secret
    """
    try:
        load_k8s_config(kubeconfig_path)
        v1 = client.CoreV1Api()

        # Delete the secret
        v1.delete_namespaced_secret(name=secret_name, namespace=namespace)

        print(f"Secret '{secret_name}' deleted successfully")
    except ApiException as e:
        print(f"Error deleting secret '{secret_name}': {e}")
        raise


def create_harness_secret(
    harness_account: str,
    harness_org: str,
    harness_project: str,
    secret_identifier: str,
    token: str,
    tags: dict[str, str] = {},
    description: str = "",
    secret_manager: str = "",
) -> bool:
    """
    Create a harness secret

    If the secret already exists, update its value
    """

    params = {
        "private_secret": "false",
        "routingId": harness_account,
        "accountIdentifier": harness_account,
    }
    if harness_org:
        params["orgIdentifier"] = harness_org
    if harness_project:
        params["projectIdentifier"] = harness_project

    payload = {
        "secret": {
            "name": secret_identifier,
            "identifier": secret_identifier,
            "tags": tags,
            "description": description,
            "type": "SecretText",
            "spec": {
                "secretManagerIdentifier": secret_manager,
                "valueType": "Inline",
                "value": token,
            },
        }
    }

    if harness_org:
        payload["secret"]["orgIdentifier"] = harness_org
    if harness_project:
        payload["secret"]["projectIdentifier"] = harness_project

    response = post(
        f"https://{check_env('PLUGIN_HARNESS_URL', 'app.harness.io')}/gateway/ng/api/v2/secrets",
        headers={
            "Harness-Account": harness_account,
            "x-api-key": check_env("PLUGIN_HARNESS_PLATFORM_API_KEY"),
        },
        params=params,
        json=payload,
    )

    try:
        response.raise_for_status()
    except Exception as e:
        print(response.text)
        raise e

    return True


def update_harness_secret(
    harness_account: str,
    harness_org: str,
    harness_project: str,
    secret_identifier: str,
    token: str,
    tags: dict[str, str] = {},
    description: str = "",
    secret_manager: str = "",
) -> bool:
    """
    Update a harness secret

    Create if not exists
    """

    params = {
        "private_secret": "false",
        "routingId": harness_account,
        "accountIdentifier": harness_account,
    }
    if harness_org:
        params["orgIdentifier"] = harness_org
    if harness_project:
        params["projectIdentifier"] = harness_project

    payload = {
        "secret": {
            "name": secret_identifier,
            "identifier": secret_identifier,
            "tags": tags,
            "description": description,
            "type": "SecretText",
            "spec": {
                "secretManagerIdentifier": secret_manager,
                "valueType": "Inline",
                "value": token,
            },
        }
    }

    if harness_org:
        payload["secret"]["orgIdentifier"] = harness_org
    if harness_project:
        payload["secret"]["projectIdentifier"] = harness_project

    response = put(
        f"https://{check_env('PLUGIN_HARNESS_URL', 'app.harness.io')}/gateway/ng/api/v2/secrets/{secret_identifier}",
        headers={
            "Harness-Account": harness_account,
            "x-api-key": check_env("PLUGIN_HARNESS_PLATFORM_API_KEY"),
        },
        params=params,
        json=payload,
    )

    try:
        response.raise_for_status()
    except Exception as e:
        try:
            data = response.json()
            if "No such secret found" in data.get("message"):
                return create_harness_secret(
                    harness_account,
                    harness_org,
                    harness_project,
                    secret_identifier,
                    token,
                    tags,
                    description,
                    secret_manager,
                )
        except Exception as f:
            raise f
        print(response.text)
        raise e

    return True


def main():
    current_unix_timestamp = int(time.time())

    namespace = check_env("PLUGIN_NAMESPACE", "harness-delegate-ng")
    service_account_name = check_env(
        "PLUGIN_SERVICE_ACCOUNT_NAME", "harness-delegate-ng"
    )

    harness_account = check_env("PLUGIN_HARNESS_ACCOUNT")
    harness_org = check_env("PLUGIN_HARNESS_ORG", None)
    harness_project = check_env("PLUGIN_HARNESS_PROJECT", None)

    secret_identifier = check_env("PLUGIN_SECRET_IDENTIFIER", service_account_name)
    secret_tags = json.loads(check_env("PLUGIN_SECRET_TAGS", "{}"))

    new_token_name = f"{service_account_name}-{current_unix_timestamp}"
    labels = {
        "harness_account": harness_account,
    }
    if harness_org:
        labels["harness_org"] = harness_org
    if harness_project:
        labels["harness_project"] = harness_project

    secrets = list_k8s_secrets(namespace, service_account_name)
    logging.info(
        f"Found {len(secrets)} existing secrets for service account {service_account_name}"
    )

    token = create_service_account_token(
        namespace, service_account_name, new_token_name, labels
    )
    logging.info(f"Created service account token: {new_token_name}")

    try:
        update_harness_secret(
            harness_account,
            harness_org,
            harness_project,
            secret_identifier,
            token,
            secret_tags,
            check_env("PLUGIN_SECRET_DESCRIPTION", "created by automation"),
            check_env("PLUGIN_SECRET_MANAGER", "harnessSecretManager"),
        )
        logging.info(f"Updated harness secret: {secret_identifier}")
    except Exception as e:
        logging.error(f"Failed to update harness secret: {e}")
        return

    if check_env("PLUGIN_DELETE_K8S_SECRETS", ""):
        for secret in secrets:
            delete_k8s_secret(namespace, secret["name"])
            logging.info(f"Deleted k8s secret: {secret['name']}")

    write_outputs(
        {
            "created_token": new_token_name,
            "updated_secret": secret_identifier,
        }
    )
    write_secret_outputs(
        {
            "token": token,
        }
    )


if __name__ == "__main__":
    main()
