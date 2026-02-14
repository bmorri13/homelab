"""Diagram 2: Data & Logging Flow — Cribl + Elastic monitoring pipeline."""

import os
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.compute import Server
from diagrams.onprem.monitoring import Splunk
from diagrams.onprem.container import Docker
from diagrams.elastic.elasticsearch import Elasticsearch, Kibana, Logstash

from config import OUTPUT_DIR, GRAPH_ATTR, NODE_ATTR, EDGE_ATTR, COLORS


def generate():
    filepath = os.path.join(OUTPUT_DIR, "data_logging_flow")

    with Diagram(
        "Data & Logging Flow",
        filename=filepath,
        show=False,
        direction="LR",
        graph_attr=GRAPH_ATTR,
        node_attr=NODE_ATTR,
        edge_attr=EDGE_ATTR,
    ):
        apps = Server("Applications\n(log sources)")

        with Cluster("monitoring-docker-compose-01 VM", graph_attr={"bgcolor": COLORS["monitoring"]}):

            with Cluster("Cribl Stream (Docker Compose)", graph_attr={"bgcolor": "#F5F5F5"}):
                cribl_worker = Docker("Cribl Worker\n:8088 HEC in\n:10200")
                cribl_leader = Docker("Cribl Leader\n:19000 UI\n:4200 mgmt")

            with Cluster("Elastic Stack (Docker Compose)", graph_attr={"bgcolor": "#FFF8E1"}):
                es = Elasticsearch("es01\n:9200")
                kibana = Kibana("Kibana\n:5601")
                logstash = Logstash("Logstash")
                filebeat = Server("Filebeat")
                metricbeat = Server("Metricbeat")

        # Primary path: Apps -> Splunk HEC -> Cribl -> Elasticsearch -> Kibana
        apps >> Edge(label="Splunk HEC\n:8088") >> cribl_worker
        cribl_worker >> Edge(label="tcp :4200") >> cribl_leader
        cribl_worker >> Edge(label="Bulk API\n:9200", color="darkgreen") >> es
        es >> Edge(label="query") >> kibana

        # Secondary paths: Beats -> ES
        filebeat >> Edge(style="dashed") >> logstash
        logstash >> Edge(style="dashed") >> es
        metricbeat >> Edge(style="dashed") >> es

    print(f"  Generated: {filepath}.png")


if __name__ == "__main__":
    generate()
