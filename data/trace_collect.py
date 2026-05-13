import json
import os
from collections import defaultdict
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Global case data directory — set by batch runner before each case
_case_data_dir = None


def set_case_data_dir(case_dir):
    """Set the data directory for the current case."""
    global _case_data_dir
    _case_data_dir = case_dir


class TraceExplorer:
    def __init__(self):
        if _case_data_dir:
            files = os.path.join(_case_data_dir, "topology", "endpoint_maps.json")
        else:
            files = os.path.join(_BASE_DIR, "data", "topology", "endpoint_maps.json")
        self.endpoint_maps = self.load_data(files)

    def load_data(self, filename):
        with open(filename, "r") as f:
            return json.load(f)

    def get_endpoint_downstream(self, endpoint, time_minute=None):
        t = self.endpoint_maps.get(endpoint, {})
        if time_minute:
            return t.get(time_minute, [])
        # Return all downstream across all minutes
        all_downstream = set()
        for minute, children in t.items():
            all_downstream.update(children)
        return sorted(all_downstream)

    def get_endpoint_downstream_in_range(self, endpoint, time_minute):
        range_stats = {}
        try:
            example_time_minute = datetime.strptime(time_minute, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            example_time_minute = datetime.strptime(time_minute, "%Y-%m-%d %H:%M")
        start_time = example_time_minute - timedelta(minutes=15)
        end_time = example_time_minute + timedelta(minutes=5)
        current_time = start_time
        while current_time <= end_time:
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:00"]:
                time_minute_str = current_time.strftime(fmt)
                if endpoint in self.endpoint_maps:
                    val = self.endpoint_maps[endpoint].get(time_minute_str)
                    if val:
                        range_stats[time_minute_str] = val
                        break
            else:
                if endpoint in self.endpoint_maps:
                    time_minute_str = current_time.strftime("%Y-%m-%d %H:%M:00")
                    range_stats[time_minute_str] = []
            current_time += timedelta(minutes=1)
        return range_stats

    def get_endpoint_upstream(self, endpoint):
        """Find all services that call this endpoint."""
        upstream = []
        for parent, minutes in self.endpoint_maps.items():
            if parent == "None":
                continue
            for minute, children in minutes.items():
                if endpoint in children:
                    upstream.append(parent)
                    break
        return sorted(set(upstream))

    def get_call_chain_for_endpoint(self, endpoint):
        """Get upstream and downstream chains for an endpoint."""
        downstream = self.get_endpoint_downstream(endpoint)
        upstream = self.get_endpoint_upstream(endpoint)
        return {
            "upstream": [(u, 1) for u in upstream],
            "downstream": [(d, 1) for d in downstream],
        }
