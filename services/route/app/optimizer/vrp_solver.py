"""
VRP Solver — Vehicle Routing Problem optimization using Google OR-Tools.

Optimizes the order of customer stops in a beat plan to minimize travel distance.
Uses a simplified distance matrix (straight-line / Haversine) since we don't
require a real routing API for the solver to work.

In production, replace distance_matrix_km() with calls to a routing API
(Google Maps Distance Matrix, OSRM, etc.).
"""
import math
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Stop:
    id: str           # customer_id
    lat: float
    lon: float
    name: str = ""


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def build_distance_matrix(stops: list[Stop]) -> list[list[int]]:
    """Build symmetric integer distance matrix (meters × 10 for OR-Tools int requirement)."""
    n = len(stops)
    matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                km = haversine_km(stops[i].lat, stops[i].lon, stops[j].lat, stops[j].lon)
                matrix[i][j] = int(km * 1000)  # Convert to meters
    return matrix


def optimize_route(
    stops: list[Stop],
    depot_index: int = 0,
    max_seconds: int = 10,
) -> list[int]:
    """
    Solve TSP/VRP for the given stops using OR-Tools.

    Args:
        stops: List of customer stops (with lat/lon)
        depot_index: Index of the depot/starting point
        max_seconds: Time limit for the solver

    Returns:
        Ordered list of stop indices (optimal visit sequence)
    """
    if len(stops) <= 1:
        return list(range(len(stops)))

    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2

        distance_matrix = build_distance_matrix(stops)
        manager = pywrapcp.RoutingIndexManager(len(stops), 1, depot_index)
        routing = pywrapcp.RoutingModel(manager)

        def distance_callback(from_idx, to_idx):
            from_node = manager.IndexToNode(from_idx)
            to_node = manager.IndexToNode(to_idx)
            return distance_matrix[from_node][to_node]

        transit_callback_index = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        search_params.time_limit.seconds = max_seconds

        solution = routing.SolveWithParameters(search_params)

        if solution:
            route = []
            index = routing.Start(0)
            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                route.append(node)
                index = solution.Value(routing.NextVar(index))
            return route
        else:
            logger.warning("OR-Tools found no solution, returning original order")
            return list(range(len(stops)))

    except ImportError:
        logger.warning("OR-Tools not installed — using greedy nearest-neighbor fallback")
        return _greedy_nearest_neighbor(stops, depot_index)


def _greedy_nearest_neighbor(stops: list[Stop], start: int) -> list[int]:
    """Greedy TSP fallback when OR-Tools is unavailable."""
    n = len(stops)
    visited = [False] * n
    route = [start]
    visited[start] = True

    for _ in range(n - 1):
        current = route[-1]
        nearest = None
        min_dist = float("inf")
        for j in range(n):
            if not visited[j]:
                d = haversine_km(
                    stops[current].lat, stops[current].lon,
                    stops[j].lat, stops[j].lon
                )
                if d < min_dist:
                    min_dist = d
                    nearest = j
        if nearest is not None:
            route.append(nearest)
            visited[nearest] = True

    return route
