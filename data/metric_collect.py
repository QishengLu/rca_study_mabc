import json
import os
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Global case data directory — set by batch runner before each case
_case_data_dir = None


def set_case_data_dir(case_dir):
    """Set the data directory for the current case."""
    global _case_data_dir
    _case_data_dir = case_dir


class MetricExplorer:
    def __init__(self):
        if _case_data_dir:
            stats_file = os.path.join(_case_data_dir, "metric", "endpoint_stats.json")
        else:
            stats_file = os.path.join(_BASE_DIR, "data", "metric", "endpoint_stats.json")
        self.aggregated_stats = self.load_data(stats_file)

    def load_data(self, filename):
        with open(filename, "r") as f:
            return json.load(f)

    def query_endpoint_stats(self, endpoint, time_minute):
        endpoint_data = self.aggregated_stats.get(endpoint, {})
        return endpoint_data.get(time_minute, {})

    def query_endpoint_stats_in_range(self, endpoint, time_minute):
        range_stats = {}
        try:
            example_time_minute = datetime.strptime(time_minute, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            example_time_minute = datetime.strptime(time_minute, "%Y-%m-%d %H:%M")
        start_time = example_time_minute - timedelta(minutes=15)
        end_time = example_time_minute + timedelta(minutes=5)
        current_time = start_time
        while current_time <= end_time:
            # Try both :SS and :00 formats
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:00"]:
                time_minute_str = current_time.strftime(fmt)
                if endpoint in self.aggregated_stats:
                    val = self.aggregated_stats[endpoint].get(time_minute_str)
                    if val:
                        range_stats[time_minute_str] = val
                        break
            else:
                if endpoint in self.aggregated_stats:
                    time_minute_str = current_time.strftime("%Y-%m-%d %H:%M:00")
                    range_stats[time_minute_str] = {
                        "calls": 0,
                        "success_rate": 0,
                        "error_rate": 0,
                        "average_duration": 0,
                        "timeout_rate": 0,
                    }
            current_time += timedelta(minutes=1)
        return range_stats
