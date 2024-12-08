import logging
import os
import json
from flask import Flask, request, jsonify
from pydantic import BaseModel, ValidationError
from kubernetes import client, config
import openai
from dotenv import load_dotenv
from openai.error import AuthenticationError, RateLimitError, InvalidRequestError, OpenAIError

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s - %(message)s',
    filename='agent.log',
    filemode='a'
)

app = Flask(__name__)

# Pydantic model for the response
class QueryResponse(BaseModel):
    query: str
    answer: str

# Load Kubernetes configuration
try:
    config.load_kube_config()  # Ensure kubeconfig is set up correctly
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()  # Adding AppsV1Api for deployments queries
except Exception as e:
    logging.error(f"Failed to load Kubernetes config: {e}")
    v1 = None
    apps_v1 = None

# Load OpenAI API key from environment variable
openai.api_key = os.getenv("OPENAI_API_KEY")

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
    "Unknown"
}

# Define available components for better classification
COMPONENTS = ["core", "database", "jobservice", "portal", "redis", "registry", "trivy"]

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
            temperature=0.0,  # For deterministic output
            n=1,
            stop=None
        )
        content = response['choices'][0]['message']['content'].strip()
        logging.debug(f"GPT Classification Response: {content}")
        
        # Safely parse the JSON response
        classification = json.loads(content)
        intent = classification.get("intent", "Unknown")
        parameters = classification.get("parameters", {})
        
        logging.debug(f"Extracted Intent: {intent}")
        logging.debug(f"Extracted Parameters: {parameters}")
        
        return intent, parameters
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error: {e}")
        return "Unknown", {}
    except Exception as e:
        logging.error(f"Error during GPT classification: {e}")
        return "Unknown", {}

def validate_parameters(intent, parameters):
    if intent in ["GetContainerPort", "GetReadinessProbePath", "GetEnvironmentVariable", "GetVolumeMountPath"]:
        component = parameters.get("component", "").lower()
        valid_components = COMPONENTS
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
        intent, params = classify_query(query)
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
            pods = v1.list_namespaced_pod(namespace=namespace)
            answer = f"There are {len(pods.items)} pods in the '{namespace}' namespace."

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
                except client.exceptions.ApiException as e:
                    answer = f"Pod '{pod_name}' not found in the namespace '{namespace}'."
            else:
                answer = "Pod name not provided in the query."

        elif intent == "ListDeployments":
            namespace = params.get("namespace", "default")
            deployments = apps_v1.list_namespaced_deployment(namespace=namespace)
            answer = f"There are {len(deployments.items)} deployments in the '{namespace}' namespace."

        elif intent == "ListServices":
            namespace = params.get("namespace", "default")
            services = v1.list_namespaced_service(namespace=namespace)
            answer = f"There are {len(services.items)} services in the '{namespace}' namespace."

        elif intent == "GetPodLogs":
            pod_name = params.get("pod_name")
            namespace = params.get("namespace", "default")
            if pod_name:
                try:
                    logs = v1.read_namespaced_pod_log(name=pod_name, namespace=namespace)
                    answer = f"Logs for pod '{pod_name}' in namespace '{namespace}':\n{logs[:200]}..."
                except client.exceptions.ApiException as e:
                    answer = f"Could not fetch logs for pod '{pod_name}' in namespace '{namespace}'."
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
                except client.exceptions.ApiException as e:
                    answer = f"Could not describe deployment '{deployment_name}' in namespace '{namespace}'."
            else:
                answer = "Deployment name not provided in the query."

        elif intent == "ListNodeNames":
            nodes = v1.list_node()
            node_names = [node.metadata.name for node in nodes.items]
            answer = f"Nodes in the cluster: {', '.join(node_names)}"

        elif intent == "GetResourceQuota":
            namespace = params.get("namespace", "default")
            quotas = v1.list_namespaced_resource_quota(namespace=namespace)
            if quotas.items:
                hard_limits = quotas.items[0].status.hard
                formatted_limits = "\n".join([f"{key}: {value}" for key, value in hard_limits.items()])
                answer = f"Resource quota for namespace '{namespace}':\n{formatted_limits}"
            else:
                answer = f"No resource quota set for namespace '{namespace}'."

        elif intent == "GetContainerPort":
            component = params.get("component")
            if component:
                try:
                    deployments = apps_v1.list_namespaced_deployment(namespace="default")
                    for deploy in deployments.items:
                        if deploy.metadata.labels.get("app.kubernetes.io/component") == component:
                            ports = deploy.spec.template.spec.containers[0].ports
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
                    deployments = apps_v1.list_namespaced_deployment(namespace="default")
                    for deploy in deployments.items:
                        if deploy.metadata.labels.get("app.kubernetes.io/component") == component:
                            readiness_probe = deploy.spec.template.spec.containers[0].readiness_probe
                            if readiness_probe and readiness_probe.http_get:
                                path = readiness_probe.http_get.path
                            else:
                                path = "N/A"
                            answer = f"The readiness probe path for '{component}' is: {path}"
                            break
                    else:
                        answer = f"No deployment found for component '{component}'."
                except Exception as e:
                    logging.error(f"Error fetching readiness probe path: {e}")
                    answer = "An error occurred while fetching readiness probe path."
            else:
                answer = "Component name not provided in the query."

        elif intent == "GetEnvironmentVariable":
            component = params.get("component")
            env_var = params.get("env_var")
            if component and env_var:
                try:
                    deployments = apps_v1.list_namespaced_deployment(namespace="default")
                    for deploy in deployments.items:
                        if deploy.metadata.labels.get("app.kubernetes.io/component") == component:
                            containers = deploy.spec.template.spec.containers
                            if containers:
                                container = containers[0]
                                for env in container.env:
                                    if env.name == env_var:
                                        answer = f"The environment variable `{env_var}` in the '{component}' pod is set to `{env.value}`."
                                        break
                                else:
                                    answer = f"Environment variable `{env_var}` not found in the '{component}' pod."
                                break
                    else:
                        answer = f"No deployment found for component '{component}'."
                except Exception as e:
                    logging.error(f"Error fetching environment variable: {e}")
                    answer = "An error occurred while fetching the environment variable."
            else:
                answer = "Component name or environment variable not provided in the query."

        elif intent == "GetVolumeMountPath":
            component = params.get("component")
            if component:
                try:
                    deployments = apps_v1.list_namespaced_deployment(namespace="default")
                    for deploy in deployments.items:
                        if deploy.metadata.labels.get("app.kubernetes.io/component") == component:
                            containers = deploy.spec.template.spec.containers
                            if containers:
                                container = containers[0]
                                volume_mounts = container.volume_mounts
                                if volume_mounts:
                                    mount_paths = ", ".join([vm.mount_path for vm in volume_mounts])
                                    answer = f"The mount path(s) for the persistent volume in '{component}' are: {mount_paths}"
                                else:
                                    answer = f"No volume mounts found for component '{component}'."
                                break
                    else:
                        answer = f"No deployment found for component '{component}'."
                except Exception as e:
                    logging.error(f"Error fetching volume mount paths: {e}")
                    answer = "An error occurred while fetching volume mount paths."
            else:
                answer = "Component name not provided in the query."

        elif intent == "GetPodsAssociatedWithSecret":
            secret_name = params.get("secret_name")
            if secret_name:
                try:
                    # Find all pods across all namespaces that use the specified secret
                    pods = v1.list_pod_for_all_namespaces(watch=False)
                    associated_pods = []
                    for pod in pods.items:
                        for volume in pod.spec.volumes:
                            if volume.secret and volume.secret.secret_name == secret_name:
                                associated_pods.append(f"{pod.metadata.namespace}/{pod.metadata.name}")
                    if associated_pods:
                        pods_list = ", ".join(associated_pods)
                        answer = f"The pod(s) associated with the secret '{secret_name}' are: {pods_list}."
                    else:
                        answer = f"No pods are associated with the secret '{secret_name}'."
                except Exception as e:
                    logging.error(f"Error fetching pods associated with secret: {e}")
                    answer = "An error occurred while fetching pods associated with the secret."
            else:
                answer = "Secret name not provided in the query."

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
