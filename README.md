**Cleric Query Agent Assignment**

**Introduction**
This project implements an AI-powered query agent that interacts with a Kubernetes cluster to answer questions about its deployed applications. The agent uses Flask for serving queries and the Kubernetes Python client for interacting with the cluster.

It supports queries such as:

The number of pods, nodes, deployments, or services.
The status of specific pods or deployments.
Fetching logs for specific pods.
Details about namespaces, resource quotas, and node names.
**Features**
Query information from a Kubernetes cluster running on Minikube.
Handles various Kubernetes resources like pods, nodes, deployments, and services.
Robust error handling for invalid or unsupported queries.
Logs all queries and responses in agent.log.
**Prerequisites**
To run this project, ensure you have the following installed:

Python: 3.10+
Docker: Version 20.10+
Minikube: Version 1.34+
Kubernetes: Version 1.25+
pip: Latest version
**Setup Instructions**
Step 1: Clone the Repository
Clone this repository to your local machine:



git clone <repository-url>
cd <repository-folder>
Step 2: Create a Virtual Environment
Set up a virtual environment for Python:


python3 -m venv venv
source venv/bin/activate  # For macOS/Linux
venv\Scripts\activate     # For Windows
Step 3: Install Dependencies
Install the required Python libraries:



pip install -r requirements.txt
Step 4: Start Minikube
Start Minikube:

minikube start --driver=docker
Verify the Minikube cluster is running:


kubectl cluster-info
Step 5: Deploy a Sample Application
Deploy the NGINX application to test:

kubectl create deployment nginx --image=nginx
kubectl scale deployment nginx --replicas=3
kubectl expose deployment nginx --type=NodePort --port=80
Verify the pods:


kubectl get pods
Running the Project
Start the Flask app:

python main.py
The application will start on http://127.0.0.1:8000.

**Test with curl or any HTTP client:**

Number of Pods:

curl -X POST "http://127.0.0.1:8000/query" -H "Content-Type: application/json" -d '{"query": "How many pods are in the default namespace?"}'
Pod Status:

curl -X POST "http://127.0.0.1:8000/query" -H "Content-Type: application/json" -d '{"query": "What is the status of the pod named nginx-676b6c5bbc-492q7?"}'
Deployments:

curl -X POST "http://127.0.0.1:8000/query" -H "Content-Type: application/json" -d '{"query": "How many deployments are in the default namespace?"}'
Pod Logs:

curl -X POST "http://127.0.0.1:8000/query" -H "Content-Type: application/json" -d '{"query": "What are the logs of the pod named nginx-676b6c5bbc-492q7?"}'
Logs
All operations are logged in agent.log. Use the following command to view logs:


cat agent.log
**Technical Specifications**
Python: 3.10+
Flask: Lightweight web framework for serving queries.
Kubernetes Python Client: To interact with the Minikube cluster.
Minikube: Kubernetes on a local machine (Version 1.34+).
Docker: Containerization platform (Version 20.10+).
**Example Queries**
Here are some supported queries:

Pods in Default Namespace:

Query: "How many pods are in the default namespace?"
Response: "There are 3 pods in the default namespace."
Pod Status:

Query: "What is the status of the pod named nginx-676b6c5bbc-492q7?"
Response: "The status of the pod 'nginx-676b6c5bbc-492q7' is Running."
Deployments in Default Namespace:

Query: "How many deployments are in the default namespace?"
Response: "There are 1 deployments in the default namespace."
Pod Logs:

Query: "What are the logs of the pod named nginx-676b6c5bbc-492q7?"
Response: "Logs for pod 'nginx-676b6c5bbc-492q7': ..." (truncated).
**Error Handling**
Logs errors in agent.log.
Returns user-friendly error messages for unsupported or invalid queries.
**Future Enhancements**
Add support for querying resource usage (CPU/Memory).
Support additional Kubernetes resources like ConfigMaps, Secrets, etc.
