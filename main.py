import logging
from flask import Flask, request, jsonify
from pydantic import BaseModel, ValidationError
from kubernetes import client, config

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

@app.route('/query', methods=['POST'])
def create_query():
    try:
        # Extracting query from the request data
        request_data = request.json
        query = request_data.get('query', "")
        logging.info(f"Received query: {query}")

        # Handling specific queries
        if "pods" in query and "default namespace" in query:
            pods = v1.list_namespaced_pod(namespace="default")
            answer = f"There are {len(pods.items)} pods in the default namespace."
        elif "nodes" in query:
            nodes = v1.list_node()
            answer = f"There are {len(nodes.items)} nodes in the cluster."
        elif "status of the pod" in query:
            try:
                pod_name = query.split("pod named ")[1].strip(" '\"?")
                logging.info(f"Extracted pod name: '{pod_name}'")
                pod = v1.read_namespaced_pod(name=pod_name, namespace="default")
                answer = f"The status of the pod '{pod_name}' is {pod.status.phase}."
            except IndexError:
                answer = "Pod name not provided in the query."
            except client.exceptions.ApiException as e:
                logging.error(f"Kubernetes API error: {e}")
                answer = f"Pod '{pod_name}' not found in the default namespace."
        elif "deployments" in query and "default namespace" in query:
            try:
                deployments = apps_v1.list_namespaced_deployment(namespace="default")
                answer = f"There are {len(deployments.items)} deployments in the default namespace."
            except client.exceptions.ApiException as e:
                logging.error(f"Kubernetes API error: {e}")
                answer = "Failed to fetch deployments in the default namespace."
        elif "services" in query and "default namespace" in query:
            services = v1.list_namespaced_service(namespace="default")
            answer = f"There are {len(services.items)} services in the default namespace."
        elif "logs of the pod" in query:
            try:
                pod_name = query.split("pod named ")[1].strip(" '\"?")
                logging.info(f"Fetching logs for pod: '{pod_name}'")
                logs = v1.read_namespaced_pod_log(name=pod_name, namespace="default")
                answer = f"Logs for pod '{pod_name}':\n{logs[:200]}..."  # Return a snippet for readability
            except client.exceptions.ApiException as e:
                logging.error(f"Kubernetes API error: {e}")
                answer = f"Could not fetch logs for pod '{pod_name}'."
        elif "namespaces" in query:
            namespaces = v1.list_namespace()
            answer = f"There are {len(namespaces.items)} namespaces in the cluster."
        elif "describe the deployment" in query:
            try:
                deployment_name = query.split("deployment named ")[1].strip(" '\"?")
                deployment = apps_v1.read_namespaced_deployment(name=deployment_name, namespace="default")
                answer = f"Deployment '{deployment_name}':\nReplicas: {deployment.spec.replicas}, Strategy: {deployment.spec.strategy.type}"
            except client.exceptions.ApiException as e:
                logging.error(f"Kubernetes API error: {e}")
                answer = f"Could not describe deployment '{deployment_name}'."
        elif "names of the nodes" in query:
            nodes = v1.list_node()
            node_names = [node.metadata.name for node in nodes.items]
            answer = f"Nodes in the cluster: {', '.join(node_names)}"
        elif "resource quota" in query and "default namespace" in query:
            quotas = v1.list_namespaced_resource_quota(namespace="default")
            if quotas.items:
                quota = quotas.items[0]
                answer = f"Resource quota for default namespace:\n{quota.status.hard}"
            else:
                answer = "No resource quota set for the default namespace."
        elif "pods" in query and "Running" in query:
            pods = v1.list_namespaced_pod(namespace="default")
            running_pods = [pod for pod in pods.items if pod.status.phase == "Running"]
            answer = f"There are {len(running_pods)} Running pods in the default namespace."
        elif "pods" in query and "label" in query:
            label = query.split("label ")[1].strip(" '\"?")
            pods = v1.list_namespaced_pod(namespace="default", label_selector=label)
            answer = f"There are {len(pods.items)} pods with label '{label}' in the default namespace."
        else:
            answer = "Query not recognized."

        logging.info(f"Generated answer: {answer}")

        # Create the response
        response = QueryResponse(query=query, answer=answer)
        return jsonify(response.dict())

    except client.exceptions.ApiException as e:
        logging.error(f"Kubernetes API error: {e}")
        return jsonify({"error": "Failed to process the query"}), 500
    except ValidationError as e:
        logging.error(f"Validation error: {e}")
        return jsonify({"error": e.errors()}), 400
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
