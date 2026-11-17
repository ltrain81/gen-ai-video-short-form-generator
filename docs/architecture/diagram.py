"""Architecture diagram for the README.

  uv run --with diagrams python docs/architecture/diagram.py      # needs Graphviz (`dot`)
Writes docs/architecture/architecture.png using the official AWS Architecture Icons (via `diagrams`).
"""
from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECS, Fargate, Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import Appsync, Eventbridge, SQS
from diagrams.aws.ml import Bedrock, Transcribe
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.security import Cognito
from diagrams.aws.storage import S3
from diagrams.generic.device import Mobile
from diagrams.onprem.client import Users

OUT = Path(__file__).with_name("architecture")
GRAPH = {"fontsize": "20", "fontname": "Helvetica", "pad": "0.4", "nodesep": "0.35", "ranksep": "1.1",
         "splines": "ortho", "bgcolor": "white", "newrank": "true"}
NODE = {"fontsize": "12", "fontname": "Helvetica"}
CL = {"fontsize": "14", "fontname": "Helvetica-Bold", "style": "rounded", "margin": "16"}

blue, purple, green, orange, grey = "#2563eb", "#7c3aed", "#059669", "#d97706", "#64748b"

with Diagram("AWS Shorts V2 — AI Video Editor", filename=str(OUT), show=False, direction="LR",
             graph_attr=GRAPH, node_attr=NODE, outformat="png"):
    users = Users("Editors\n(browser)")

    with Cluster("Frontend", graph_attr={**CL, "bgcolor": "#f8fafc"}):
        cdn = CloudFront("CloudFront\nReact + Cloudscape")
        auth = Cognito("Cognito\n(+ OIDC/SAML)")

    with Cluster("API & realtime", graph_attr={**CL, "bgcolor": "#eff6ff"}):
        api = APIGateway("HTTP API\n(Cognito JWT)")
        api_fn = Lambda("API\narchive · ACL · projects\nmanual ops · renders")
        events = Appsync("AppSync Events\nper-user channels")

    with Cluster("Data", graph_attr={**CL, "bgcolor": "#f0fdf4"}):
        media = S3("Media bucket\noriginals · proxies\nrenders (private)")
        table = Dynamodb("DynamoDB\nsingle table")
        vectors = S3("S3 Vectors\nindex per tenant")

    with Cluster("AI editor (orchestrator)", graph_attr={**CL, "bgcolor": "#f5f3ff"}):
        broker = Lambda("Session broker")
        with Cluster("Bedrock Managed Agents\n(OpenAI Codex harness)", graph_attr={**CL, "bgcolor": "#ede9fe"}):
            bma = Bedrock("BMA session\ngpt-5.6-luna")
            runtime = ECS("AgentCore Runtime\nmicroVM\n(skills + MCP proxy)")
        tools_api = APIGateway("Tools API\n(AWS_IAM: runtime role)")
        tools = Lambda("Agent tools\nsearch · look · framing\nsnap · edit_timeline")
        strands = Lambda("Strands fallback\n(same tools)")

    with Cluster("Ingest & render (event-driven)", graph_attr={**CL, "bgcolor": "#fffbeb"}):
        bus = Eventbridge("EventBridge\nObject Created")
        q = SQS("SQS queues")
        coord = Lambda("Coordinator")
        worker = Fargate("Media worker\nffmpeg · libass\nprepare · render")
        model_fn = Lambda("Model workers\n+ poller")

    with Cluster("Amazon Bedrock models", graph_attr={**CL, "bgcolor": "#fdf2f8"}):
        pegasus = Bedrock("Pegasus 1.5\nvideo understanding")
        marengo = Bedrock("Marengo 3.0\nembeddings")
        vision = Bedrock("OpenAI vision\nframing · captions")
        stt = Transcribe("Transcribe\nword timestamps")

    # Request path
    users >> Edge(color=grey, label="app") >> cdn
    users >> Edge(color=grey, style="dashed") >> auth
    users >> Edge(color=blue, label="REST / chat") >> api >> api_fn
    api_fn >> Edge(color=green) >> table
    api_fn >> Edge(color=green, label="presigned URLs") >> media
    events >> Edge(color=blue, style="dashed", label="live events") >> users

    # AI editor
    api_fn >> Edge(color=purple, label="turn") >> broker >> Edge(color=purple) >> bma >> Edge(color=purple) >> runtime
    runtime >> Edge(color=purple, label="SigV4 + binding") >> tools_api >> tools
    broker >> Edge(color=purple, style="dashed", label="fallback") >> strands
    broker >> Edge(color=blue, style="dashed") >> events
    tools >> Edge(color=orange) >> vision
    tools >> Edge(color=green) >> vectors

    # Ingest & render
    media >> Edge(color=orange, label="upload") >> bus >> q >> coord
    coord >> Edge(color=orange) >> worker
    coord >> Edge(color=orange) >> model_fn
    model_fn >> Edge(color=orange) >> pegasus
    model_fn >> Edge(color=orange) >> marengo
    model_fn >> Edge(color=orange) >> stt
