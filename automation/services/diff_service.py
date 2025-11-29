from typing import Dict, Any


class DiffService:
    def diff(
        self, local_dsl: Dict[str, Any], remote_dsl: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Compare local DSL with remote DSL and return a summary of changes.
        Focuses on nodes and edges.
        """
        local_nodes = self._get_nodes_map(local_dsl)
        remote_nodes = self._get_nodes_map(remote_dsl)

        local_ids = set(local_nodes.keys())
        remote_ids = set(remote_nodes.keys())

        added_ids = local_ids - remote_ids
        removed_ids = remote_ids - local_ids
        common_ids = local_ids & remote_ids

        modified_ids = []
        for node_id in common_ids:
            if self._is_node_modified(
                local_nodes[node_id], remote_nodes[node_id]
            ):
                modified_ids.append(node_id)

        return {
            "nodes": {
                "added": list(added_ids),
                "removed": list(removed_ids),
                "modified": modified_ids,
                "total_local": len(local_ids),
                "total_remote": len(remote_ids)
            },
            "has_changes": bool(added_ids or removed_ids or modified_ids)
        }

    def _get_nodes_map(self, dsl: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        nodes = dsl.get("workflow", {}).get("graph", {}).get("nodes", [])
        return {n["id"]: n for n in nodes if "id" in n}

    def _is_node_modified(
        self, local_node: Dict[str, Any], remote_node: Dict[str, Any]
    ) -> bool:
        # Compare data fields relevant to logic
        # Ignoring position for logic diff usually, but let's include it
        # if strict. For now, let's compare 'data' and 'type'

        # Simple equality check on data
        # We might want to ignore some fields like 'selected' or UI state
        return (
            local_node.get("type") != remote_node.get("type") or
            local_node.get("data") != remote_node.get("data")
        )
