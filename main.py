import logging
from flask import Flask, request, jsonify
from pydantic import BaseModel, ValidationError
from kubernetes import client, config
import openai
from dotenv import load_dotenv
import os
 
# Configure logging
load_dotenv()
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s - %(message)s',
                    filename='agent.log', filemode='a')
 
app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")
 
try:
    config.load_kube_config()
    logging.info("Kubernetes configuration loaded successfully.")
except Exception as e:
    logging.error("Failed to load Kubernetes configuration.")
    raise e
 
class QueryResponse(BaseModel):
    query: str
    answer: str
 
def handle_kubernetes_query(query: str) -> str:
    try:
        v1 = client.CoreV1Api()
       
        # Check for Harbor registry status
        print("Query is ", query)
        if "harbor" in query:
            print("Query has harbor")
            # Assuming Harbor is deployed as a service or pod, you can query for its status
            services = v1.list_service_for_all_namespaces()
            harbor_service = next((service for service in services.items if "harbor" in service.metadata.name.lower()), None)
            if harbor_service:
                return f"The Harbor registry service '{harbor_service.metadata.name}' is running in the cluster."
            else:
                return "The Harbor registry service is not found in the cluster."
       
        # Check for pods in the default namespace
        if "pods" in query and "default namespace" in query:
            pods = v1.list_namespaced_pod(namespace="default")
            return f"There are {len(pods.items)} pods in the default namespace."
       
        # Check for services in the cluster
        elif "services" in query:
            services = v1.list_service_for_all_namespaces()
            return f"There are {len(services.items)} services in the cluster."
       
        # Check for namespaces in the cluster
        elif "namespaces" in query:
            namespaces = v1.list_namespace()
            return f"The cluster has {len(namespaces.items)} namespaces."
       
        else:
            return "I'm unable to process this query. Please rephrase or provide more context."
    except Exception as e:
        logging.error(f"Error processing Kubernetes query: {e}")
        return "There was an error interacting with the Kubernetes cluster."
 
@app.route('/query', methods=['POST'])
def create_query():
    try:
        # Extract the question from the request data
        request_data = request.json
        query = request_data.get('query')
       
        # Log the question
        logging.info(f"Received query: {query}")
       
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
       
        except Exception as e:
            logging.error(f"Failed to generate answer: {e}")
            raise e
       
        # Log the answer
       
       
        answer = handle_kubernetes_query(gpt_analysis)
        print("Answer is ", answer)
        # Create the response model
        response = QueryResponse(query=query, answer=gpt_analysis)
        print("Response is ", response)
        logging.info(f"Generated answer: {answer}")
        return jsonify(response.dict())
   
    except ValidationError as e:
        return jsonify({"error": e.errors()}), 400
 
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
has context menu
