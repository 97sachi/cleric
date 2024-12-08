# main.py

import os
import logging
from flask import Flask, request, jsonify
from pydantic import BaseModel, ValidationError
from kubernetes import client, config
import openai
from dotenv import load_dotenv
import re

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s - %(message)s',
    filename='agent.log',
    filemode='a'
)

app = Flask(__name__)

# Define Pydantic model for response
class QueryResponse(BaseModel):
    query: str
    answer: str

# Initialize OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    logging.error("OpenAI API key not found. Please set OPENAI_API_KEY in .env file.")
    raise EnvironmentError("OpenAI API key not found.")

# Initialize Kubernetes client
try:
    config.load_kube_config(config_file=os.path.expanduser("~/.kube/config"))
    k8s_client = client.CoreV1Api()
    apps_v1_client = client.AppsV1Api()
    logging.info("Successfully loaded Kubernetes configuration.")
except Exception as e:
    logging.error(f"Failed to load Kubernetes configuration: {e}")
    raise e

@app.route('/query', methods=['POST'])
def create_query():
    try:
        # Extract the question from the request data
        request_data = request.json
        query = request_data.get('query')

        if not query:
            logging.warning("No query found in the request.")
            return jsonify({"error": "No query provided."}), 400

        # Log the question
        logging.info(f"Received query: {query}")

        # Process the query and fetch Kubernetes data
        k8s_info = process_k8s_query(query)

        # Generate a natural language answer using GPT-4
        answer = generate_answer(query, k8s_info)

        # Log the answer
        logging.info(f"Generated answer: {answer}")

        # Create the response model
        response = QueryResponse(query=query, answer=answer)

        return jsonify(response.dict()), 200

    except ValidationError as e:
        logging.error(f"Validation error: {e}")
        return jsonify({"error": e.errors()}), 400
    except ValueError as ve:
        logging.warning(f"Value error: {ve}")
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return jsonify({"error": "Internal Server Error"}), 500

def process_k8s_query(query: str) -> dict:
    """
    Process the query and fetch relevant information from Kubernetes.
    """
    response_data = {}
    try:
        query_lower = query.lower()

        # Query 1: Which namespace is the harbor service deployed to?
        if "which namespace" in query_lower and "harbor service" in query_lower:
            namespaces = get_service_namespaces("harbor")
            response_data['harbor_namespace'] = namespaces

        # Query 2: How many pods are in the cluster?
        elif "how many pods" in query_lower and "cluster" in query_lower:
            total_pods = get_total_pod_count()
            response_data['total_pods'] = total_pods

        # Query 3: What is the container port for harbor-core?
        elif "container port" in query_lower and "harbor-core" in query_lower:
            container_port = get_container_port("harbor-core")
            response_data['harbor_core_container_port'] = container_port

        # Query 4: What is the status of harbor registry?
        elif "status of" in query_lower and "harbor registry" in query_lower:
            status = get_deployment_status("harbor-registry")
            response_data['harbor_registry_status'] = status

        # Query 5: Which port will the harbor redis service route traffic to?
        elif "harbor redis service" in query_lower and "route traffic to" in query_lower:
            redis_service_port = get_service_route_port("harbor-redis")
            response_data['harbor_redis_service_port'] = redis_service_port

        # Query 6: What is the readiness probe path for the harbor core?
        elif "readiness probe path" in query_lower and "harbor core" in query_lower:
            readiness_path = get_readiness_probe_path("harbor-core")
            response_data['harbor_core_readiness_path'] = readiness_path

        # Query 7: Which pod(s) are associated with the harbor database secret?
        elif "pods are associated with" in query_lower and "harbor database secret" in query_lower:
            pods = get_pods_associated_with_secret("harbor-database-secret")
            response_data['pods_associated_with_harbor_db_secret'] = pods

        else:
            logging.warning(f"Unrecognized query format: {query}")
            raise ValueError("Unrecognized query format.")

    except Exception as e:
        logging.error(f"Error processing query: {e}")
        raise e

    return response_data

# Helper Functions

def get_service_namespaces(service_name: str) -> list:
    """
    Retrieve all namespaces where a specific service is deployed.
    """
    try:
        services = k8s_client.list_service_for_all_namespaces(field_selector=f"metadata.name={service_name}")
        namespaces = [svc.metadata.namespace for svc in services.items]
        if not namespaces:
            raise ValueError(f"Service '{service_name}' not found in any namespace.")
        return namespaces
    except Exception as e:
        logging.error(f"Error fetching namespaces for service '{service_name}': {e}")
        raise e

def get_total_pod_count() -> int:
    """
    Retrieve the total number of pods in the entire cluster across all namespaces.
    """
    try:
        pods = k8s_client.list_pod_for_all_namespaces()
        return len(pods.items)
    except Exception as e:
        logging.error(f"Error fetching total pod count: {e}")
        raise e

def get_container_port(deployment_name: str) -> int:
    """
    Retrieve the container port for a specific deployment.
    """
    try:
        deployments = apps_v1_client.list_deployment_for_all_namespaces(field_selector=f"metadata.name={deployment_name}")
        if not deployments.items:
            raise ValueError(f"Deployment '{deployment_name}' not found.")
        deployment = deployments.items[0]
        containers = deployment.spec.template.spec.containers
        if not containers:
            raise ValueError(f"No containers found in deployment '{deployment_name}'.")
        # Assuming the first container
        if not containers[0].ports:
            raise ValueError(f"No ports defined for container in deployment '{deployment_name}'.")
        container_port = containers[0].ports[0].container_port
        return container_port
    except Exception as e:
        logging.error(f"Error fetching container port for deployment '{deployment_name}': {e}")
        raise e

def get_deployment_status(deployment_name: str) -> str:
    """
    Retrieve the status of a specific deployment.
    """
    try:
        namespace = extract_namespace_from_deployment(deployment_name)
        deployment = apps_v1_client.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace
        )
        conditions = deployment.status.conditions
        if not conditions:
            raise ValueError(f"No status conditions found for deployment '{deployment_name}'.")
        # Get the latest condition
        latest_condition = conditions[-1].type  # e.g., Available, Progressing
        return latest_condition
    except Exception as e:
        logging.error(f"Error fetching status for deployment '{deployment_name}': {e}")
        raise e

def extract_namespace_from_deployment(deployment_name: str) -> str:
    """
    Find the namespace of a given deployment.
    """
    try:
        deployments = apps_v1_client.list_deployment_for_all_namespaces(field_selector=f"metadata.name={deployment_name}")
        if not deployments.items:
            raise ValueError(f"Deployment '{deployment_name}' not found.")
        return deployments.items[0].metadata.namespace
    except Exception as e:
        logging.error(f"Error extracting namespace for deployment '{deployment_name}': {e}")
        raise e

def get_service_route_port(service_name: str) -> int:
    """
    Retrieve the port to which a specific service routes traffic.
    """
    try:
        services = k8s_client.list_service_for_all_namespaces(field_selector=f"metadata.name={service_name}")
        if not services.items:
            raise ValueError(f"Service '{service_name}' not found.")
        service = services.items[0]
        if not service.spec.ports:
            raise ValueError(f"No ports defined for service '{service_name}'.")
        # Assuming the first port
        route_port = service.spec.ports[0].port
        return route_port
    except Exception as e:
        logging.error(f"Error fetching route port for service '{service_name}': {e}")
        raise e

def get_readiness_probe_path(deployment_name: str) -> str:
    """
    Retrieve the readiness probe path for a specific deployment's container.
    """
    try:
        namespace = extract_namespace_from_deployment(deployment_name)
        deployment = apps_v1_client.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace
        )
        containers = deployment.spec.template.spec.containers
        if not containers:
            raise ValueError(f"No containers found in deployment '{deployment_name}'.")
        readiness_probe = containers[0].readiness_probe
        if not readiness_probe or not readiness_probe.http_get:
            raise ValueError(f"No readiness probe configured for deployment '{deployment_name}'.")
        if not readiness_probe.http_get.path:
            raise ValueError(f"No readiness probe path defined for deployment '{deployment_name}'.")
        return readiness_probe.http_get.path
    except Exception as e:
        logging.error(f"Error fetching readiness probe path for deployment '{deployment_name}': {e}")
        raise e

def get_pods_associated_with_secret(secret_name: str) -> list:
    """
    Retrieve all pods that are using a specific secret.
    """
    try:
        pods = k8s_client.list_pod_for_all_namespaces()
        associated_pods = []
        for pod in pods.items:
            # Check if any volume is using the secret
            for volume in pod.spec.volumes:
                if volume.secret and volume.secret.secret_name == secret_name:
                    associated_pods.append(pod.metadata.name)
                    break  # Avoid duplicates
            # Check environment variables from secret
            for container in pod.spec.containers:
                for env in container.env or []:
                    if env.value_from and env.value_from.secret_key_ref and env.value_from.secret_key_ref.name == secret_name:
                        associated_pods.append(pod.metadata.name)
                        break
        if not associated_pods:
            raise ValueError(f"No pods are associated with the secret '{secret_name}'.")
        return list(set(associated_pods))  # Remove duplicates
    except Exception as e:
        logging.error(f"Error fetching pods associated with secret '{secret_name}': {e}")
        raise e

def generate_answer(query: str, k8s_info: dict) -> str:
    """
    Use GPT-4 to generate a natural language answer based on Kubernetes data.
    """
    prompt = f"""
You are an AI assistant that provides concise answers about Kubernetes cluster resources.

Query: {query}

Kubernetes Information: {k8s_info}

Provide a clear and direct answer based only on the Kubernetes Information provided.
Do not include any additional information or identifiers.
"""

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an assistant that provides concise and accurate answers based on provided data."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.2,
        )
        answer = response['choices'][0]['message']['content'].strip()
        return answer
    except Exception as e:
        logging.error(f"Error generating answer with GPT-4: {e}")
        raise e

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
