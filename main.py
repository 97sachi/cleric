import logging
import os
from flask import Flask, request, jsonify
from pydantic import BaseModel, ValidationError
from kubernetes import client, config
import openai

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
    config.load_kube_config()  # Ensuring kubeconfig is set up correctly
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()  # Adding AppsV1Api for deployments queries
except Exception as e:
    logging.error(f"Failed to load Kubernetes config: {e}")
    v1 = None  # Handling cases where Kubernetes client cannot be initialized
    apps_v1 = None

# Load OpenAI API key from environment variable
openai.api_key = os.getenv("OPENAI_API_KEY")
@app.route('/query', methods=['POST'])
def create_query():
    
    try:
        # Extracting query from the request data
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
        except openai.error.OpenAIError as e:
            logging.error(f"OpenAI API error: {e}")
            return jsonify({"error": "OpenAI API error occurred"}), 500

        # Handle specific Kubernetes queries based on GPT analysis
        answer = "Query not recognized."
        if "pods" in gpt_analysis and "default namespace" in gpt_analysis:
            pods = v1.list_namespaced_pod(namespace="default")
            answer = f"There are {len(pods.items)} pods in the default namespace."
        elif "nodes" in gpt_analysis:
            nodes = v1.list_node()
            answer = f"There are {len(nodes.items)} nodes in the cluster."
        elif "status of the pod" in gpt_analysis:
            try:
                pod_name = query.split("pod named ")[1].strip(" '\"?")
                pod = v1.read_namespaced_pod(name=pod_name, namespace="default")
                answer = f"The status of the pod '{pod_name}' is {pod.status.phase}."
            except IndexError:
                answer = "Pod name not provided in the query."
            except client.exceptions.ApiException:
                answer = f"Pod '{pod_name}' not found in the default namespace."
        elif "deployments" in gpt_analysis and "default namespace" in gpt_analysis:
            deployments = apps_v1.list_namespaced_deployment(namespace="default")
            answer = f"There are {len(deployments.items)} deployments in the default namespace."
        elif "services" in gpt_analysis and "default namespace" in gpt_analysis:
            services = v1.list_namespaced_service(namespace="default")
            answer = f"There are {len(services.items)} services in the default namespace."
        elif "logs of the pod" in gpt_analysis:
            try:
                pod_name = query.split("pod named ")[1].strip(" '\"?")
                logs = v1.read_namespaced_pod_log(name=pod_name, namespace="default")
                answer = f"Logs for pod '{pod_name}':\n{logs[:200]}..."
            except client.exceptions.ApiException:
                answer = f"Could not fetch logs for pod '{pod_name}'."
        elif "namespaces" in gpt_analysis:
            namespaces = v1.list_namespace()
            answer = f"There are {len(namespaces.items)} namespaces in the cluster."
        elif "describe the deployment" in gpt_analysis:
            try:
                deployment_name = query.split("deployment named ")[1].strip(" '\"?")
                deployment = apps_v1.read_namespaced_deployment(name=deployment_name, namespace="default")
                answer = f"Deployment '{deployment_name}':\nReplicas: {deployment.spec.replicas}, Strategy: {deployment.spec.strategy.type}"
            except client.exceptions.ApiException:
                answer = f"Could not describe deployment '{deployment_name}'."
        elif "names of the nodes" in gpt_analysis:
            nodes = v1.list_node()
            node_names = [node.metadata.name for node in nodes.items]
            answer = f"Nodes in the cluster: {', '.join(node_names)}"
        elif "resource quota" in gpt_analysis and "default namespace" in gpt_analysis:
            quotas = v1.list_namespaced_resource_quota(namespace="default")
            answer = f"Resource quota for default namespace:\n{quotas.items[0].status.hard}" if quotas.items else "No resource quota set."
        elif "pods" in gpt_analysis and "Running" in gpt_analysis:
            pods = v1.list_namespaced_pod(namespace="default")
            running_pods = [pod for pod in pods.items if pod.status.phase == "Running"]
            answer = f"There are {len(running_pods)} Running pods in the default namespace."
        elif "pods" in gpt_analysis and "label" in gpt_analysis:
            label = query.split("label ")[1].strip(" '\"?")
            pods = v1.list_namespaced_pod(namespace="default", label_selector=label)
            answer = f"There are {len(pods.items)} pods with label '{label}' in the default namespace."

        # Return the answer
        logging.info(f"Generated answer: {answer}")
        response = QueryResponse(query=query, answer=answer)
        return jsonify(response.dict())

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
