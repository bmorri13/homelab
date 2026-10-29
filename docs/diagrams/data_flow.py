"""Diagram 3: Data flow — every source → Cribl HEC → Splunk → detections, dashboards, agents, Slack."""

import os
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.compute import Server
from diagrams.onprem.monitoring import Splunk
from diagrams.onprem.container import Docker
from diagrams.generic.storage import Storage
from diagrams.generic.blank import Blank
from diagrams.saas.chat import Slack
from diagrams.k8s.compute import Cronjob

from config import OUTPUT_DIR, GRAPH_ATTR, NODE_ATTR, EDGE_ATTR, COLORS, SOURCES, SPLUNK_INDEXES, NETWORK


def generate():
    filepath = os.path.join(OUTPUT_DIR, "data_flow")
    graph = {**GRAPH_ATTR, "nodesep": "0.25", "ranksep": "1.2"}

    with Diagram(
        "Data flow",
        filename=filepath,
        show=False,
        direction="LR",
        graph_attr=graph,
        node_attr=NODE_ATTR,
        edge_attr=EDGE_ATTR,
    ):
        with Cluster("Sources (OTel or syslog → HEC)", graph_attr={"bgcolor": COLORS["network"]}):
            sources = [Server(s) for s in SOURCES]

        with Cluster("security-tooling-01", graph_attr={"bgcolor": COLORS["monitoring"]}):
            cribl = Docker(f"Cribl Stream\nhec_in {NETWORK['hec']}\n• secret masking\n• Falco → OCSF + MITRE\n• logins → linux_secure\n• routing, noise drops")
            indexes = Storage("Splunk indexes\n" + ", ".join(SPLUNK_INDEXES[:4]) + "\n" + ", ".join(SPLUNK_INDEXES[4:]))
            with Cluster("Splunk"):
                detections = Splunk("18 ATT&CK v20 detections\nevery 15 min →\nhomelab:detection findings")
                alerts = Splunk("Alerts\nFalco, honeypot,\nsecret leaks")
                dashboards = Splunk("Dashboards\nHomelab Ops, Homelab Security")
                watchdog = Splunk("Agents watchdog\n:05 / :35")

        agents = Cronjob("Homelab agents (k3s-01)\nsec :00/:30, ops :15/:45")
        slack = Slack("Slack\nalert + digest cards")

        for s in sources:
            s >> cribl
        cribl >> indexes
        indexes >> [detections, alerts, dashboards, watchdog]
        detections >> Edge(label="findings queue") >> agents
        alerts >> Edge(label="fired alerts") >> agents
        agents >> Edge(label="cards") >> slack
        watchdog >> Edge(color="firebrick", label="only direct post") >> slack

    print(f"  Generated: {filepath}.png")


if __name__ == "__main__":
    generate()
