import logging
import os
from flask import Flask, request, jsonify
from pydantic import BaseModel
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

@app.route('/query', methods=['POST'])
def create_query():
    try:
        # Extract query from the request data
        request_data = request.json
        query = request_data.get('query', "")
        logging.info(f"Received query: {query}")

        # Validate Kubernetes client initialization
        if v1 is None or apps_v1 is None:
            logging.error("Kubernetes client not initialized")
            return jsonify({"error": "Kubernetes client not initialized"}), 500

        # Use OpenAI to analyze query intent
        try:
            gpt_response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a Kubernetes assistant."},
                    {"role": "user", "content": query}
                ],
                max_tokens=100,
                temperature=0.2
            )
            gpt_analysis = gpt_response['choices'][0]['message']['content'].strip()
            logging.info(f"GPT Analysis: {gpt_analysis}")
        except (AuthenticationError, RateLimitError, InvalidRequestError, OpenAIError) as e:
            logging.error(f"OpenAI API error: {e}")
            return jsonify({"error": f"OpenAI API error: {e}"}), 500

        # Handle specific Kubernetes queries based on GPT analysis
        answer = "Query not recognized."
        try:
            if "namespace" in gpt_analysis and "harbor service" in gpt_analysis:
                services = v1.list_service_for_all_namespaces()
                harbor_namespace = next(
                    (svc.metadata.namespace for svc in services.items if "harbor" in svc.metadata.name), None
                )
                answer = f"The harbor service is deployed in the '{harbor_namespace}' namespace." if harbor_namespace else "Harbor service not found."

            elif "pods in the cluster" in gpt_analysis:
                pods = v1.list_pod_for_all_namespaces()
                answer = f"There are {len(pods.items)} pods in the cluster."

            elif "container port for harbor-core" in gpt_analysis:
                pods = v1.list_namespaced_pod(namespace="default", label_selector="app=harbor-core")
                container_ports = pods.items[0].spec.containers[0].ports if pods.items else []
                harbor_core_port = next((port.container_port for port in container_ports if port.name == "http"), None)
                answer = f"The container port for harbor-core is {harbor_core_port}." if harbor_core_port else "Harbor-core port not found."

            elif "status of harbor registry" in gpt_analysis:
                pods = v1.list_namespaced_pod(namespace="default", label_selector="app=harbor-registry")
                status = pods.items[0].status.phase if pods.items else "Not found"
                answer = f"The status of the harbor registry is {status}."

            elif "port for the harbor redis svc" in gpt_analysis:
                service = v1.read_namespaced_service(name="harbor-redis", namespace="default")
                ports = [port.port for port in service.spec.ports]
                answer = f"The harbor redis service routes traffic to port {ports[0]}." if ports else "No port found for harbor-redis service."

            elif "readiness probe path for harbor core" in gpt_analysis:
                pods = v1.list_namespaced_pod(namespace="default", label_selector="app=harbor-core")
                readiness_probe = pods.items[0].spec.containers[0].readiness_probe.http_get.path if pods.items else "Not found"
                answer = f"The readiness probe path for harbor-core is '{readiness_probe}'." if readiness_probe else "Readiness probe path not configured."

            elif "harbor database secret" in gpt_analysis:
                pods = v1.list_pod_for_all_namespaces()
                associated_pods = [
                    pod.metadata.name for pod in pods.items
                    if any(env.name == "harbor-database-secret" for container in pod.spec.containers for env in (container.env or []))
                ]
                answer = f"The pods associated with harbor-database-secret are: {', '.join(associated_pods)}." if associated_pods else "No pods associated with harbor-database-secret."

            elif "mount path of the persistent volume" in gpt_analysis:
                pods = v1.list_namespaced_pod(namespace="default", label_selector="app=harbor-database")
                mount_paths = [vol_mount.mount_path for vol_mount in pods.items[0].spec.containers[0].volume_mounts]
                answer = f"The mount path of the persistent volume is: {mount_paths[0]}." if mount_paths else "No mount path found."

            elif "CHART_CACHE_DRIVER in the harbor core pod" in gpt_analysis:
                pods = v1.list_namespaced_pod(namespace="default", label_selector="app=harbor-core")
                env_vars = {env.name: env.value for env in pods.items[0].spec.containers[0].env}
                answer = f"The value of CHART_CACHE_DRIVER is {env_vars.get('CHART_CACHE_DRIVER', 'not set')}."

            elif "name of the database in PostgreSQL" in gpt_analysis:
                pods = v1.list_namespaced_pod(namespace="default", label_selector="app=harbor-database")
                db_name_env = [env.value for env in pods.items[0].spec.containers[0].env if env.name == "DATABASE_NAME"]
                answer = f"The Harbor database name is '{db_name_env[0]}'." if db_name_env else "Database name not found."

        except Exception as e:
            logging.error(f"Error processing Kubernetes API query: {e}")
            answer = "An error occurred while processing the query."

        # Return the answer
        logging.info(f"Generated answer: {answer}")
        response = QueryResponse(query=query, answer=answer)
        return jsonify(response.dict())

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
