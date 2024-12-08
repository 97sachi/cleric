import os
import logging
from flask import Flask, request, jsonify
from pydantic import BaseModel, ValidationError
from typing import Optional
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import openai

# Initialize Flask app
app = Flask(__name__)

# Configure logging
logging.basicConfig(
    filename='agent.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)

# Define possible intents
INTENTS = {
    "ListNamespaces",
    "ListPods",
    "ListNodes",
    "GetPodStatus",
    "ListDeployments",
    "ListServices",
    "GetPodLogs",
    "DescribeDeployment",
    "ListNodeNames",
    "GetResourceQuota",
    "GetContainerPort",
    "GetReadinessProbePath",
    "GetEnvironmentVariable",
    "GetVolumeMountPath",
    "GetPodsAssociatedWithSecret",
    "GetServiceNamespace",
    "GetDeploymentStatus",
    "Unknown"
}

# Define available components
COMPONENTS = ["core", "database", "jobservice", "portal", "redis", "registry", "trivy"]

# Define the response model
class QueryResponse(BaseModel):
    query: str
    answer: str

# Load environment variables
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

if not OPENAI_API_KEY:
    logging.error("OpenAI API key not found in environment variables.")
    raise ValueError("OpenAI API key not found in environment variables.")

openai.api_key = OPENAI_API_KEY

# Initialize Kubernetes client
try:
    config.load_kube_config()  # For local development
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
except Exception as e:
    logging.error(f"Failed to initialize Kubernetes client: {e}")
    v1 = None
    apps_v1 = None

def classify_query(query):
    prompt = f"""
You are a Kubernetes assistant. Classify the following user query into one of the predefined intents and extract any necessary parameters.

### Intents:
- ListNamespaces
- ListPods
- ListNodes
- GetPodStatus
- ListDeployments
- ListServices
- GetPodLogs
- DescribeDeployment
- ListNodeNames
- GetResourceQuota
- GetContainerPort
- GetReadinessProbePath
- GetEnvironmentVariable
- GetVolumeMountPath
- GetPodsAssociatedWithSecret
- GetServiceNamespace
- GetDeploymentStatus

### Available Components:
{', '.join(COMPONENTS)}

If the query does not match any of the above intents, classify it as "Unknown".

### Example Queries and Responses:

#### Query:
"How many pods are running in the default namespace?"

#### Response:
{{
    "intent": "ListPods",
    "parameters": {{
        "namespace": "default"
    }}
}}

#### Query:
"What is the container port for harbor-core?"

#### Response:
{{
    "intent": "GetContainerPort",
    "parameters": {{
        "component": "core"
    }}
}}

#### Query:
"Describe the deployment 'harbor-core' in the default namespace."

#### Response:
{{
    "intent": "DescribeDeployment",
    "parameters": {{
        "deployment_name": "harbor-core",
        "namespace": "default"
    }}
}}

#### Query:
"What is the value of the environment variable CHART_CACHE_DRIVER in the harbor core pod?"

#### Response:
{{
    "intent": "GetEnvironmentVariable",
    "parameters": {{
        "component": "core",
        "env_var": "CHART_CACHE_DRIVER"
    }}
}}

#### Query:
"What is the mount path of the persistent volume for the harbor database?"

#### Response:
{{
    "intent": "GetVolumeMountPath",
    "parameters": {{
        "component": "database"
    }}
}}

#### Query:
"Which pod(s) associate with the harbor database secret?"

#### Response:
{{
    "intent": "GetPodsAssociatedWithSecret",
    "parameters": {{
        "secret_name": "harbor-database"
    }}
}}

#### Query:
"Which namespace is the harbor service deployed to?"

#### Response:
{{
    "intent": "GetServiceNamespace",
    "parameters": {{
        "service_name": "harbor"
    }}
}}

#### Query:
"How many pods are in the cluster?"

#### Response:
{{
    "intent": "ListPods",
    "parameters": {{
        "namespace": "all"
    }}
}}

#### Query:
"What is the status of harbor registry?"

#### Response:
{{
    "intent": "GetDeploymentStatus",
    "parameters": {{
        "deployment_name": "harbor-registry",
        "namespace": "default"
    }}
}}

#### Query:
"Which port will the harbor redis svc route traffic to?"

#### Response:
{{
    "intent": "GetContainerPort",
    "parameters": {{
        "component": "harbor-redis"
    }}
}}

#### Query:
"What is the readiness probe path for the harbor core?"

#### Response:
{{
    "intent": "GetReadinessProbePath",
    "parameters": {{
        "component": "core"
    }}
}}

### User Query:
"{query}"

### Response Format (JSON):
{{
    "intent": "<IntentName>",
    "parameters": {{
        "namespace": "<namespace>",        # Optional
        "pod_name": "<pod_name>",          # Optional
        "deployment_name": "<deployment_name>",  # Optional
        "service_name": "<service_name>",          # Optional
        "label": "<label_selector>",                # Optional
        "secret_name": "<secret_name>",            # Optional
        "component": "<component>",                # Optional
        "env_var": "<environment_variable>"         # Optional
    }}
}}
"""
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.0,
            n=1,
            stop=None
        )
        classification = response['choices'][0]['message']['content']
        logging.debug(f"GPT Classification Response: {classification}")
        return classification
    except Exception as e:
        logging.error(f"Error during OpenAI API call: {e}")
        return '{"intent": "Unknown", "parameters": {}}'

def validate_parameters(intent, parameters):
    if intent in ["GetContainerPort", "GetReadinessProbePath", "GetEnvironmentVariable", "GetVolumeMountPath"]:
        component = parameters.get("component", "").lower()
        valid_components = COMPONENTS + ["harbor-redis"]  # Added "harbor-redis" as valid component
        if component not in valid_components:
            return False, f"Invalid component name '{component}'. Valid components are: {', '.join(valid_components)}."
    elif intent == "GetPodsAssociatedWithSecret":
        secret_name = parameters.get("secret_name", "").strip()
        if not secret_name:
            return False, "Secret name not provided in the query."
    elif intent in ["GetPodStatus", "GetPodLogs"]:
        pod_name = parameters.get("pod_name", "").strip()
        if not pod_name:
            return False, "Pod name not provided in the query."
    elif intent in ["GetServiceNamespace", "DescribeDeployment", "GetDeploymentStatus"]:
        deployment_name = parameters.get("deployment_name", "").strip()
        if not deployment_name:
            return False, "Deployment name not provided in the query."
    # Add more validation rules as needed for other intents
    return True, ""

def preprocess_parameters(intent, parameters):
    if intent in ["GetContainerPort", "GetReadinessProbePath", "GetEnvironmentVariable", "GetVolumeMountPath"]:
        component = parameters.get("component", "").lower()
        if component.startswith("harbor-"):
            component = component.replace("harbor-", "")
            parameters["component"] = component
    return parameters

@app.route('/query', methods=['POST'])
def create_query():
    try:
        # Extract query from the request data
        request_data = request.json
        query = request_data.get('query', "").strip()
        logging.info(f"Received query: {query}")

        # Validate Kubernetes client initialization
        if v1 is None or apps_v1 is None:
            logging.error("Kubernetes client not initialized")
            return jsonify({"error": "Kubernetes client not initialized"}), 500

        # Classify the query using GPT
        classification = classify_query(query)
        # Parse the JSON response
        classification_json = client.utils.json.loads(classification)
        intent = classification_json.get("intent", "Unknown")
        params = classification_json.get("parameters", {})
        logging.info(f"Classified intent: {intent}, parameters: {params}")

        # Validate parameters
        is_valid, validation_msg = validate_parameters(intent, params)
        if not is_valid:
            logging.warning(f"Parameter validation failed: {validation_msg}")
            answer = validation_msg
            response = QueryResponse(query=query, answer=answer)
            return jsonify(response.dict())

        # Preprocess parameters
        params = preprocess_parameters(intent, params)
        logging.debug(f"Preprocessed parameters: {params}")

        # Initialize answer
        answer = "Query not recognized."

        # Handle specific Kubernetes queries based on intent
        if intent == "ListNamespaces":
            namespaces = v1.list_namespace()
            answer = f"There are {len(namespaces.items)} namespaces in the cluster."

        elif intent == "ListPods":
            namespace = params.get("namespace", "default")
            if namespace.lower() == "all":
                try:
                    pods = v1.list_pod_for_all_namespaces()
                    pod_count = len(pods.items)
                    answer = f"There are {pod_count} pods in the entire cluster."
                except Exception as e:
                    logging.error(f"Error fetching pods across all namespaces: {e}")
                    answer = "An error occurred while fetching pods across all namespaces."
            else:
                try:
                    # Verify if the namespace exists
                    v1.read_namespace(name=namespace)
                    pods = v1.list_namespaced_pod(namespace=namespace)
                    pod_count = len(pods.items)
                    answer = f"There are {pod_count} pods in the '{namespace}' namespace."
                except ApiException as e:
                    if e.status == 404:
                        answer = f"Namespace '{namespace}' not found."
                    else:
                        answer = f"An error occurred: {e.reason}"
                except Exception as e:
                    logging.error(f"Error fetching pods in namespace '{namespace}': {e}")
                    answer = "An error occurred while fetching pods."

        elif intent == "ListNodes":
            nodes = v1.list_node()
            answer = f"There are {len(nodes.items)} nodes in the cluster."

        elif intent == "GetPodStatus":
            pod_name = params.get("pod_name")
            namespace = params.get("namespace", "default")
            if pod_name:
                try:
                    pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
                    answer = f"The status of the pod '{pod_name}' in namespace '{namespace}' is {pod.status.phase}."
                except ApiException as e:
                    if e.status == 404:
                        answer = f"Pod '{pod_name}' not found in the namespace '{namespace}'."
                    else:
                        answer = f"An error occurred: {e.reason}"
                except Exception as e:
                    logging.error(f"Error fetching pod status: {e}")
                    answer = "An error occurred while fetching pod status."
            else:
                answer = "Pod name not provided in the query."

        elif intent == "ListDeployments":
            namespace = params.get("namespace", "default")
            try:
                deployments = apps_v1.list_namespaced_deployment(namespace=namespace)
                deployment_count = len(deployments.items)
                answer = f"There are {deployment_count} deployments in the '{namespace}' namespace."
            except ApiException as e:
                if e.status == 404:
                    answer = f"Namespace '{namespace}' not found."
                else:
                    answer = f"An error occurred: {e.reason}"
            except Exception as e:
                logging.error(f"Error fetching deployments in namespace '{namespace}': {e}")
                answer = "An error occurred while fetching deployments."

        elif intent == "ListServices":
            service_name = params.get("service_name")
            if service_name:
                try:
                    # Search for the service across all namespaces
                    services = v1.list_service_for_all_namespaces()
                    found = False
                    for svc in services.items:
                        if svc.metadata.name.lower() == service_name.lower():
                            namespace = svc.metadata.namespace
                            answer = f"The service '{service_name}' is deployed in the '{namespace}' namespace."
                            found = True
                            break
                    if not found:
                        answer = f"Service '{service_name}' not found in any namespace."
                except Exception as e:
                    logging.error(f"Error fetching service details: {e}")
                    answer = "An error occurred while fetching the service details."
            else:
                # If no service_name is provided, list services in a specified namespace
                namespace = params.get("namespace", "default")
                try:
                    # Verify if the namespace exists
                    v1.read_namespace(name=namespace)
                    services = v1.list_namespaced_service(namespace=namespace)
                    service_count = len(services.items)
                    answer = f"There are {service_count} services in the '{namespace}' namespace."
                except ApiException as e:
                    if e.status == 404:
                        answer = f"Namespace '{namespace}' not found."
                    else:
                        answer = f"An error occurred: {e.reason}"
                except Exception as e:
                    logging.error(f"Error fetching services in namespace '{namespace}': {e}")
                    answer = "An error occurred while fetching services."

        elif intent == "GetPodLogs":
            pod_name = params.get("pod_name")
            namespace = params.get("namespace", "default")
            if pod_name:
                try:
                    logs = v1.read_namespaced_pod_log(name=pod_name, namespace=namespace)
                    # Limit logs to the first 1000 characters for brevity
                    answer = f"Logs for pod '{pod_name}' in namespace '{namespace}':\n{logs[:1000]}{'...' if len(logs) > 1000 else ''}"
                except ApiException as e:
                    if e.status == 404:
                        answer = f"Pod '{pod_name}' not found in the namespace '{namespace}'."
                    else:
                        answer = f"Could not fetch logs: {e.reason}"
                except Exception as e:
                    logging.error(f"Error fetching pod logs: {e}")
                    answer = "An error occurred while fetching pod logs."
            else:
                answer = "Pod name not provided in the query."

        elif intent == "DescribeDeployment":
            deployment_name = params.get("deployment_name")
            namespace = params.get("namespace", "default")
            if deployment_name:
                try:
                    deployment = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
                    replicas = deployment.spec.replicas
                    strategy = deployment.spec.strategy.type
                    answer = f"Deployment '{deployment_name}' in namespace '{namespace}':\nReplicas: {replicas}, Strategy: {strategy}"
                except ApiException as e:
                    if e.status == 404:
                        answer = f"Deployment '{deployment_name}' not found in the namespace '{namespace}'."
                    else:
                        answer = f"Could not describe deployment: {e.reason}"
                except Exception as e:
                    logging.error(f"Error describing deployment: {e}")
                    answer = "An error occurred while describing the deployment."
            else:
                answer = "Deployment name not provided in the query."

        elif intent == "ListNodeNames":
            nodes = v1.list_node()
            node_names = [node.metadata.name for node in nodes.items]
            answer = f"Nodes in the cluster: {', '.join(node_names)}"

        elif intent == "GetResourceQuota":
            namespace = params.get("namespace", "default")
            try:
                quotas = v1.list_namespaced_resource_quota(namespace=namespace)
                if quotas.items:
                    hard_limits = quotas.items[0].status.hard
                    formatted_limits = "\n".join([f"{key}: {value}" for key, value in hard_limits.items()])
                    answer = f"Resource quota for namespace '{namespace}':\n{formatted_limits}"
                else:
                    answer = f"No resource quota set for namespace '{namespace}'."
            except ApiException as e:
                if e.status == 404:
                    answer = f"Namespace '{namespace}' not found."
                else:
                    answer = f"An error occurred: {e.reason}"
            except Exception as e:
                logging.error(f"Error fetching resource quotas: {e}")
                answer = "An error occurred while fetching resource quotas."

        elif intent == "GetContainerPort":
            component = params.get("component")
            if component:
                try:
                    # Normalize component name
                    component_normalized = component.lower()
                    # Search for the deployment with the specified component
                    deployments = apps_v1.list_namespaced_deployment(namespace="default")
                    for deploy in deployments.items:
                        deploy_component = deploy.metadata.labels.get("app.kubernetes.io/component", "").lower()
                        if deploy_component == component_normalized:
                            containers = deploy.spec.template.spec.containers
                            if containers:
                                container = containers[0]
                                ports = container.ports
                                if ports:
                                    port_info = ", ".join([f"{p.container_port}/{p.protocol}" for p in ports])
                                    answer = f"The container port(s) for '{component}' are: {port_info}"
                                    break
                    else:
                        answer = f"No deployment found for component '{component}'."
                except Exception as e:
                    logging.error(f"Error fetching container ports: {e}")
                    answer = "An error occurred while fetching container ports."
            else:
                answer = "Component name not provided in the query."

        elif intent == "GetReadinessProbePath":
            component = params.get("component")
            if component:
                try:
                    component_normalized = component.lower()
                    deployments = apps_v1.list_namespaced_deployment(namespace="default")
                    for deploy in deployments.items:
                        deploy_component = deploy.metadata.labels.get("app.kubernetes.io/component", "").lower()
                        if deploy_component == component_normalized:
                            containers = deploy.spec.template.spec.containers
                            if containers:
                                container = containers[0]
                                readiness_probe = container.readiness_probe
                                if readiness_probe and readiness_probe.http_get:
                                    path = readiness_probe.http_get.path
                                    answer = f"The readiness probe path for '{component}' is: {path}"
                                    break
                    else:
                        answer = f"No deployment found for component '{component}'."
                except Exception as e:
                    logging.error(f"Error fetching readiness probe path: {e}")
                    answer = "An error occurred while fetching readiness probe path."
            else:
                answer = "Component name not provided in the query."

        elif intent == "GetDeploymentStatus":
            deployment_name = params.get("deployment_name")
            namespace = params.get("namespace", "default")
            if deployment_name:
                try:
                    deployment = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
                    available_replicas = deployment.status.available_replicas or 0
                    desired_replicas = deployment.spec.replicas or 0
                    answer = f"Deployment '{deployment_name}' in namespace '{namespace}' has {available_replicas}/{desired_replicas} replicas available."
                except ApiException as e:
                    if e.status == 404:
                        answer = f"Deployment '{deployment_name}' not found in the namespace '{namespace}'."
                    else:
                        answer = f"Could not fetch deployment status: {e.reason}"
                except Exception as e:
                    logging.error(f"Error fetching deployment status: {e}")
                    answer = "An error occurred while fetching deployment status."
            else:
                answer = "Deployment name not provided in the query."

        elif intent == "Unknown":
            answer = "I'm sorry, I didn't understand your query. Could you please rephrase it?"

        else:
            answer = "Query not recognized."

        # Return the answer
        logging.info(f"Generated answer: {answer}")
        response = QueryResponse(query=query, answer=answer)
        return jsonify(response.dict())

    except ValidationError as ve:
        logging.error(f"Validation error: {ve}")
        return jsonify({"error": "Invalid input format"}), 400
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
