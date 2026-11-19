"""Kleine, deterministische Traversierung der ausdrücklich gepflegten Projektbeziehungen."""

from collections import deque

from jarvis.project_connections import ProjectConnections

RELATIONS = {
    "project_person", "project_task", "project_document", "person_task",
    "person_evidence", "person_alias", "alias_evidence", "task_evidence",
    "project_memory", "task_deadline",
}
MAX_DEPTH = 3
MAX_PATHS = 20
MAX_EXPANSIONS = 1000
MAX_NODES = 100
MAX_EDGES = 200


class ProjectGraph:
    def __init__(self, connections: ProjectConnections):
        self.connections = connections

    @staticmethod
    def _filters(relations: str | None) -> set[str] | None:
        if relations is None or not relations.strip():
            return None
        selected = {item.strip() for item in relations.split(",")}
        if "" in selected or not selected <= RELATIONS:
            raise ValueError("Unbekannter Beziehungstyp im Filter")
        return selected

    def snapshot(self, state_id: str) -> dict:
        data = self.connections.for_state(state_id)
        nodes = [{"id": f"project:{state_id}", "kind": "project", "label": data["project"]}]
        edges = []

        def edge(edge_id: str, origin: str, target: str, relation: str,
                 role: str | None = None, source_note: str | None = None,
                 source_status: str | None = None, source_document_id: str | None = None,
                 source_sha256: str | None = None,
                 source_path: str | None = None,
                 confidence: str | None = None, validity_status: str | None = None,
                 status: str | None = None,
                 merged_from_person_name: str | None = None,
                 merged_from_source_status: str | None = None,
                 merged_from_validity_status: str | None = None,
                 memory_source_ref: str | None = None,
                 memory_policy: str | None = None,
                 memory_sha256: str | None = None) -> None:
            edges.append({"id": edge_id, "from": origin, "to": target,
                          "relation": relation, "role": role, "source_note": source_note,
                          "source_status": source_status,
                          "source_document_id": source_document_id,
                          "source_sha256": source_sha256,
                          "source_path": source_path,
                          "confidence": confidence, "validity_status": validity_status,
                          "status": status,
                          "merged_from_person_name": merged_from_person_name,
                          "merged_from_source_status": merged_from_source_status,
                          "merged_from_validity_status": merged_from_validity_status,
                          "memory_source_ref": memory_source_ref,
                          "memory_policy": memory_policy,
                          "memory_sha256": memory_sha256})

        documents = {item["id"] for item in data["documents"]}
        people = {item["id"] for item in data["people"]}
        tasks = {item["id"] for item in data["tasks"]}
        for person in data["people"]:
            key = f"person:{person['id']}"
            nodes.append({"id": key, "kind": "person", "label": person["name"],
                          "source_status": person["source_status"],
                          "validity_status": person["validity_status"]})
            edge(f"project_person:{person['id']}", f"project:{state_id}", key,
                 "project_person", person["relation_type"], person["source_note"],
                 person["source_status"], person["source_document_id"],
                 person["source_sha256"], person["source_path"], person["confidence"],
                 person["validity_status"])
            if person["source_document_id"] in documents:
                edge(f"person_evidence:{person['id']}", key,
                     f"document:{person['source_document_id']}", "person_evidence",
                     source_note=person["source_note"], source_status=person["source_status"],
                     source_document_id=person["source_document_id"],
                     source_sha256=person["source_sha256"],
                     source_path=person["source_path"],
                     confidence=person["confidence"], validity_status=person["validity_status"])
        for task in data["tasks"]:
            key = f"task:{task['id']}"
            nodes.append({"id": key, "kind": "task", "label": task["title"],
                          "status": task["status"]})
            edge(f"project_task:{task['id']}", f"project:{state_id}", key,
                 "project_task", status=task["status"])
            local_due = task.get("current_due_local") or task.get("due_local")
            if local_due:
                deadline_key = f"deadline:{task['id']}"
                zone = task.get("current_timezone") or task.get("timezone")
                offset = task.get("current_due_offset") or task.get("due_offset")
                nodes.append({"id": deadline_key, "kind": "deadline",
                              "label": f"{local_due} {zone or ''} {offset or ''}".strip(),
                              "status": task.get("due_status")})
                edge(f"task_deadline:{task['id']}", key, deadline_key,
                     "task_deadline", source_note="Fälligkeit der ausgewählten Projektaufgabe",
                     status=task.get("due_status"))
        for document in data["documents"]:
            key = f"document:{document['id']}"
            nodes.append({"id": key, "kind": "document", "label": document["path"],
                          "status": document["status"], "sha256": document["sha256"]})
            edge(f"project_document:{document['id']}", f"project:{state_id}", key,
                 "project_document", source_status=document["status"],
                 source_document_id=document["id"], source_sha256=document["sha256"],
                 source_path=document["path"])
        for link in data["person_tasks"]:
            if link["task_id"] not in tasks or link["person_id"] not in people:
                continue
            edge(f"person_task:{link['id']}", f"task:{link['task_id']}",
                 f"person:{link['person_id']}", "person_task", link["relation_type"],
                 link["source_note"], link["source_status"], link["source_document_id"],
                 link["source_sha256"], link["source_path"], link["confidence"], link["validity_status"],
                 link["status"], link["merged_from_person_name"],
                 link["merged_from_source_status"], link["merged_from_validity_status"])
            if link["source_document_id"] in documents:
                edge(f"task_evidence:{link['id']}", f"task:{link['task_id']}",
                     f"document:{link['source_document_id']}", "task_evidence",
                     source_note=link["source_note"], source_status=link["source_status"],
                     source_document_id=link["source_document_id"],
                     source_sha256=link["source_sha256"],
                     source_path=link["source_path"],
                     confidence=link["confidence"], validity_status=link["validity_status"],
                     status=link["status"], merged_from_person_name=link["merged_from_person_name"],
                     merged_from_source_status=link["merged_from_source_status"],
                     merged_from_validity_status=link["merged_from_validity_status"])
        for alias in data["aliases"]:
            if alias["person_id"] not in people:
                continue
            key = f"alias:{alias['id']}"
            nodes.append({"id": key, "kind": "alias", "label": alias["alias"],
                          "source_status": alias["source_status"],
                          "validity_status": alias["validity_status"]})
            edge(f"person_alias:{alias['id']}", f"person:{alias['person_id']}", key,
                 "person_alias", source_note=alias["source_note"],
                 source_status=alias["source_status"],
                 source_document_id=alias["source_document_id"],
                 source_sha256=alias["source_sha256"],
                 source_path=alias["source_path"],
                 confidence=alias["confidence"], validity_status=alias["validity_status"])
            if alias["source_document_id"] in documents:
                edge(f"alias_evidence:{alias['id']}", key,
                     f"document:{alias['source_document_id']}", "alias_evidence",
                     source_note=alias["source_note"], source_status=alias["source_status"],
                     source_document_id=alias["source_document_id"],
                     source_sha256=alias["source_sha256"],
                     source_path=alias["source_path"],
                     confidence=alias["confidence"], validity_status=alias["validity_status"])
        for memory in data.get("memories", []):
            key = f"memory:{memory['memory_id']}"
            nodes.append({"id": key, "kind": "memory", "label": memory["memory_text"][:120],
                          "status": memory["memory_status"]})
            edge(f"project_memory:{memory['id']}", f"project:{state_id}", key,
                 "project_memory", source_note=memory["source_note"],
                 source_status=memory["memory_status"], confidence=memory["confidence"],
                 validity_status=memory["validity_status"], status=memory["memory_status"],
                 memory_source_ref=memory["memory_source_ref"],
                 memory_policy=memory["memory_policy"],
                 memory_sha256=memory["memory_sha256"])
        nodes.sort(key=lambda item: item["id"])
        edges.sort(key=lambda item: (item["relation"], item["id"]))
        if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
            raise ValueError("Projektgraph überschreitet die lokale Sicherheitsgrenze")
        return {"project_id": state_id, "state_version": data["state_version"],
                "checked_at": data["checked_at"], "nodes": nodes, "edges": edges}

    def neighbors(self, state_id: str, node_id: str, relations: str | None = None,
                  direction: str = "both") -> dict:
        selected = self._filters(relations)
        if direction not in {"in", "out", "both"}:
            raise ValueError("Richtung muss in, out oder both sein")
        graph = self.snapshot(state_id)
        node_map = {item["id"]: item for item in graph["nodes"]}
        if node_id not in node_map:
            raise ValueError("Knoten gehört nicht zu diesem Projekt")
        edges = [item for item in graph["edges"] if
                 (selected is None or item["relation"] in selected) and (
                     (direction in {"out", "both"} and item["from"] == node_id) or
                     (direction in {"in", "both"} and item["to"] == node_id))]
        adjacent = {item["to"] if item["from"] == node_id else item["from"] for item in edges}
        return {"project_id": state_id, "node": node_map[node_id],
                "neighbors": [node_map[key] for key in sorted(adjacent)], "edges": edges,
                "checked_at": graph["checked_at"]}

    def paths(self, state_id: str, source_id: str, target_id: str,
              max_depth: int = MAX_DEPTH, relations: str | None = None) -> dict:
        if not 1 <= max_depth <= MAX_DEPTH:
            raise ValueError("Pfadtiefe muss zwischen 1 und 3 liegen")
        selected = self._filters(relations)
        graph = self.snapshot(state_id)
        node_map = {item["id"]: item for item in graph["nodes"]}
        if source_id not in node_map or target_id not in node_map:
            raise ValueError("Knoten gehört nicht zu diesem Projekt")
        if source_id == target_id:
            raise ValueError("Start- und Zielknoten müssen verschieden sein")
        outgoing = {}
        for edge in graph["edges"]:
            if selected is None or edge["relation"] in selected:
                outgoing.setdefault(edge["from"], []).append(edge)
        queue = deque([(source_id, (source_id,), ())])
        found = []
        expansions = 0
        limit_reached = False
        while queue and len(found) < MAX_PATHS and expansions < MAX_EXPANSIONS:
            node_id, path_nodes, path_edges = queue.popleft()
            if len(path_edges) >= max_depth:
                continue
            for edge in outgoing.get(node_id, []):
                if expansions >= MAX_EXPANSIONS:
                    limit_reached = True
                    break
                expansions += 1
                next_id = edge["to"]
                if next_id in path_nodes:
                    continue
                next_nodes = (*path_nodes, next_id)
                next_edges = (*path_edges, edge)
                if next_id == target_id:
                    found.append({"nodes": [node_map[key] for key in next_nodes],
                                  "edges": list(next_edges)})
                    if len(found) >= MAX_PATHS:
                        limit_reached = True
                        break
                elif len(next_edges) < max_depth:
                    queue.append((next_id, next_nodes, next_edges))
        return {"project_id": state_id, "source": node_map[source_id],
                "target": node_map[target_id], "max_depth": max_depth,
                "paths": found, "truncated": limit_reached or bool(queue) or expansions >= MAX_EXPANSIONS,
                "checked_at": graph["checked_at"]}
